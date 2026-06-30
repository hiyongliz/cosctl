from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import typer
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

DEFAULT_VERSION = "0.1.3"
DOWNLOAD_RETRY_COUNT = 10


app = typer.Typer(
    short_help="Command line tool for managing Tencent Cloud COS",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich",
    add_completion=False,
    rich_help_panel="cosctl",
    help="Command line tool for managing Tencent Cloud COS (Cloud Object Storage)",
)


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing."""


def get_version() -> str:
    """Return the installed package version when available."""
    try:
        return version("cosctl")
    except PackageNotFoundError:
        return DEFAULT_VERSION


def version_callback(value: bool) -> None:
    """Print the current version and exit."""
    if value:
        typer.echo(get_version())
        raise typer.Exit()


def create_progress(action: str) -> Progress:
    """Create a Rich progress bar for a transfer action."""
    return Progress(
        TextColumn(f"[bold blue]{action}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        transient=False,
    )


def progress_callback(progress: Progress, task_id: TaskID) -> Callable[[int, int], None]:
    """Adapt a Rich progress task to the COS SDK callback signature."""

    def callback(consumed_bytes: int, total_bytes: int) -> None:
        progress.update(task_id, completed=consumed_bytes, total=total_bytes or None)

    return callback


def read_environment() -> dict[str, str | None]:
    """Read COS configuration from environment variables.

    BUCKET falls back to the first label of DOMAIN (e.g. ``my-bucket.cos.ap-beijing.myqcloud.com``
    yields ``my-bucket``) when the BUCKET environment variable is not set.
    """
    settings: dict[str, str | None] = {
        "secret_id": os.getenv("SECRET_ID"),
        "secret_key": os.getenv("SECRET_KEY"),
        "region": os.getenv("REGION", "wuxi"),
        "domain": os.getenv("DOMAIN"),
        "bucket": os.getenv("BUCKET"),
    }
    missing_variables: list[str] = []
    if not settings["secret_id"]:
        missing_variables.append("SECRET_ID")
    if not settings["secret_key"]:
        missing_variables.append("SECRET_KEY")
    if not settings["bucket"]:
        if settings["domain"]:
            settings["bucket"] = settings["domain"].split(".", 1)[0]
        else:
            missing_variables.append("BUCKET")

    if missing_variables:
        raise ConfigError("Please set {} environment variables.".format(", ".join(missing_variables)))

    return settings


def create_client(settings: dict[str, str | None]) -> CosS3Client:
    """Create a COS client from validated settings."""
    config = CosConfig(
        Region=settings["region"],
        SecretId=settings["secret_id"],
        SecretKey=settings["secret_key"],
        Token=None,
        Domain=settings["domain"],
    )
    return CosS3Client(config)


def create_client_and_bucket() -> tuple[CosS3Client, str]:
    """Create a COS client and return it with the configured bucket."""
    settings = read_environment()
    bucket = settings["bucket"]
    if bucket is None:
        raise ConfigError("Please set BUCKET environment variables.")
    return create_client(settings), bucket


def size_formater(size: str) -> str:
    """Format object size to a human-readable string."""
    size_int = int(size)
    if size_int < 1024:
        return f"{size_int}B"
    if size_int < 1024**2:
        return f"{size_int / 1024:.2f}KB"
    if size_int < 1024**3:
        return f"{size_int / (1024**2):.2f}MB"
    return f"{size_int / (1024**3):.2f}GB"


def default_object_name(file_path: str) -> str:
    """Return the default object name for a local file."""
    return Path(file_path).name


def default_file_path(obj_name: str) -> str:
    """Return the default local file path for an object key."""
    return Path(obj_name.rstrip("/")).name or obj_name


def command_error(error: Exception) -> None:
    """Print a command error message and exit with a non-zero status."""
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=1)


def parse_tags(tags: list[str]) -> dict:
    """Parse --tag key=value pairs into the COS Tagging dict.

    Raises ValueError on malformed pairs or when more than 10 tags are given
    (COS enforces a 10-tag-per-object limit).
    """
    tag_list: list[dict[str, str]] = []
    for item in tags:
        if "=" not in item:
            raise ValueError(f"Invalid tag {item!r}, expected key=value")
        key, _, value = item.partition("=")
        if not key:
            raise ValueError(f"Invalid tag {item!r}, key cannot be empty")
        tag_list.append({"Key": key, "Value": value})
    if len(tag_list) > 10:
        raise ValueError("Too many tags, COS supports up to 10 tags per object")
    return {"TagSet": {"Tag": tag_list}}


def download_object(client: CosS3Client, bucket: str, obj_name: str, filepath: str) -> None:
    """Download an object with retry support."""
    last_error: Exception | None = None
    progress = create_progress("Downloading")
    with progress:
        task = progress.add_task("download", total=None)
        for _ in range(DOWNLOAD_RETRY_COUNT):
            try:
                client.download_file(
                    Bucket=bucket,
                    Key=obj_name,
                    DestFilePath=filepath,
                    progress_callback=progress_callback(progress, task),
                )
                return
            except (CosClientError, CosServiceError) as error:
                last_error = error
                progress.update(task, completed=0)

    if last_error is not None:
        raise last_error

    raise RuntimeError("Download failed without a specific error.")


@app.command()
def upload(
    file: str,
    obj_name: str = typer.Argument(""),
    tag: list[str] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Object tag as key=value; repeat to add multiple tags (max 10).",
    ),
) -> None:
    """Upload a file to the bucket."""
    if obj_name == "":
        obj_name = default_object_name(file)

    typer.echo(f"Uploading {file} to {obj_name}")

    try:
        client, bucket = create_client_and_bucket()
        start_time = datetime.now()
        progress = create_progress("Uploading")
        with progress:
            task = progress.add_task("upload", total=None)
            response = client.upload_file(
                Bucket=bucket,
                LocalFilePath=file,
                Key=obj_name,
                PartSize=10,
                MAXThread=10,
                progress_callback=progress_callback(progress, task),
            )
    except (ConfigError, CosClientError, CosServiceError, OSError) as error:
        command_error(error)

    typer.echo(f"Upload completed in {datetime.now() - start_time}\n{response}")

    if tag:
        try:
            tagging = parse_tags(tag)
            client.put_object_tagging(Bucket=bucket, Key=obj_name, Tagging=tagging)
        except (ValueError, CosClientError, CosServiceError) as error:
            command_error(error)
        typer.echo(f"Tagged {obj_name} with {len(tag)} tag(s)")


@app.command()
def get(obj_name: str, filepath: str = "") -> None:
    """Download an object from the bucket."""
    if filepath == "":
        filepath = default_file_path(obj_name)

    typer.echo(f"Downloading {obj_name} to {filepath}")

    try:
        client, bucket = create_client_and_bucket()
        start_time = datetime.now()
        download_object(client, bucket, obj_name, filepath)
    except (ConfigError, CosClientError, CosServiceError, OSError) as error:
        command_error(error)

    typer.echo(f"Download completed in {datetime.now() - start_time}")


@app.command(name="list")
def list_objects(
    prefix: str = typer.Argument("", help="Only list object keys starting with this prefix."),
) -> None:
    """List all objects in the bucket."""
    try:
        client, bucket = create_client_and_bucket()
        marker = ""
        contents: list[dict[str, str]] = []
        while True:
            response = client.list_objects(
                Bucket=bucket,
                Prefix=prefix,
                Marker=marker,
                MaxKeys=100,
            )
            if "Contents" in response:
                contents.extend(response["Contents"])

            if response["IsTruncated"] == "false":
                break
            marker = response["NextMarker"]
    except (ConfigError, CosClientError, CosServiceError) as error:
        command_error(error)

    typer.echo(f"total {len(contents)}")
    for content in contents:
        typer.echo(f"{content['LastModified']} {size_formater(content['Size']):>10} {content['Key']}")


@app.command()
def delete(obj_name: str) -> None:
    """Delete an object from the bucket."""
    typer.echo(f"Deleting {obj_name}")

    try:
        client, bucket = create_client_and_bucket()
        client.delete_object(
            Bucket=bucket,
            Key=obj_name,
        )
    except (ConfigError, CosClientError, CosServiceError) as error:
        command_error(error)

    typer.echo(f"Object {obj_name} deleted successfully.")


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Command line tool for managing Tencent Cloud COS (Cloud Object Storage)."""


if __name__ == "__main__":
    app()
