from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.progress import Progress
from typer.testing import CliRunner

from cosctl.main import (
    app,
    create_progress,
    get_version,
    progress_callback,
    size_formater,
)

runner = CliRunner()


class TestSizeFormater:
    def test_bytes(self):
        assert size_formater("12") == "12B"

    def test_kilobytes(self):
        assert size_formater("2048") == "2.00KB"

    def test_megabytes(self):
        assert size_formater(str(3 * 1024 * 1024)) == "3.00MB"


class TestProgress:
    def test_create_progress_returns_rich_progress(self):
        progress = create_progress("Uploading")
        assert isinstance(progress, Progress)

    def test_callback_updates_task_completed_and_total(self):
        progress = create_progress("Uploading")
        with progress:
            task = progress.add_task("upload", total=None)
            callback = progress_callback(progress, task)

            callback(500, 1000)

            assert progress.tasks[task].completed == 500
            assert progress.tasks[task].total == 1000

            callback(1000, 1000)

            assert progress.tasks[task].completed == 1000
            assert progress.tasks[task].finished

    def test_callback_with_zero_total_keeps_unknown(self):
        progress = create_progress("Uploading")
        with progress:
            task = progress.add_task("upload", total=None)
            callback = progress_callback(progress, task)

            callback(100, 0)

            assert progress.tasks[task].total is None
            assert progress.tasks[task].completed == 100


class TestVersion:
    def test_returns_installed_version(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("cosctl.main.version", lambda _: "0.1.1+20260402.123045.abc1234")

        assert get_version() == "0.1.1+20260402.123045.abc1234"


class TestCli:
    def test_version(self):
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert "0.1.3" in result.stdout

    def test_help(self):
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "Command line tool for managing Tencent Cloud COS" in result.stdout

    def test_list_requires_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SECRET_ID", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("BUCKET", raising=False)
        monkeypatch.delenv("DOMAIN", raising=False)

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 1
        assert "SECRET_ID, SECRET_KEY, BUCKET" in result.stderr

    def test_bucket_falls_back_to_domain_label(self, monkeypatch: pytest.MonkeyPatch):
        import cosctl.main as main_module

        monkeypatch.setenv("SECRET_ID", "id")
        monkeypatch.setenv("SECRET_KEY", "key")
        monkeypatch.delenv("BUCKET", raising=False)
        monkeypatch.setenv("DOMAIN", "backup-1255000021.cos.wuxi.myqcloud.com")

        captured: dict[str, str | None] = {}

        class FakeClient:
            def list_objects(self, **kwargs):
                captured["Bucket"] = kwargs["Bucket"]
                return {"Contents": [], "IsTruncated": "false"}

        monkeypatch.setattr(
            main_module,
            "create_client_and_bucket",
            lambda: (FakeClient(), main_module.read_environment()["bucket"]),
        )

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert captured["Bucket"] == "backup-1255000021"

    def test_get_does_not_report_completed_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("SECRET_ID", "id")
        monkeypatch.setenv("SECRET_KEY", "key")
        monkeypatch.setenv("BUCKET", "bucket")

        import cosctl.main as main_module

        class DownloadFailed(Exception):
            pass

        monkeypatch.setattr(main_module, "CosClientError", DownloadFailed)
        monkeypatch.setattr(
            main_module,
            "create_client_and_bucket",
            lambda: (SimpleNamespace(), "bucket"),
        )

        def fake_download_object(*args, **kwargs):
            raise DownloadFailed("network failed")

        monkeypatch.setattr(main_module, "download_object", fake_download_object)

        result = runner.invoke(app, ["get", "remote-file.txt"])

        assert result.exit_code == 1
        assert "Download completed" not in result.stdout
        assert "network failed" in result.stderr

    def test_list_outputs_all_pages(self, monkeypatch: pytest.MonkeyPatch):
        import cosctl.main as main_module

        class FakeListClient:
            def __init__(self):
                self.calls = []

            def list_objects(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return {
                        "Contents": [
                            {
                                "LastModified": "2025-01-01T00:00:00Z",
                                "Size": "12",
                                "Key": "a.txt",
                            }
                        ],
                        "IsTruncated": "true",
                        "NextMarker": "page-2",
                    }
                return {
                    "Contents": [
                        {
                            "LastModified": "2025-01-02T00:00:00Z",
                            "Size": "2048",
                            "Key": "b.txt",
                        }
                    ],
                    "IsTruncated": "false",
                }

        client = FakeListClient()
        monkeypatch.setattr(
            main_module,
            "create_client_and_bucket",
            lambda: (client, "bucket"),
        )

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "total 2" in result.stdout
        assert "a.txt" in result.stdout
        assert "b.txt" in result.stdout
        assert client.calls[0]["Bucket"] == "bucket"
        assert client.calls[0]["Marker"] == ""
        assert client.calls[1]["Marker"] == "page-2"

    def test_delete_success(self, monkeypatch: pytest.MonkeyPatch):
        import cosctl.main as main_module

        class FakeDeleteClient:
            def __init__(self):
                self.calls = []

            def delete_object(self, **kwargs):
                self.calls.append(kwargs)

        client = FakeDeleteClient()
        monkeypatch.setattr(
            main_module,
            "create_client_and_bucket",
            lambda: (client, "bucket"),
        )

        result = runner.invoke(app, ["delete", "remote-file.txt"])

        assert result.exit_code == 0
        assert "deleted successfully" in result.stdout
        assert client.calls == [{"Bucket": "bucket", "Key": "remote-file.txt"}]

    def test_delete_failure(self, monkeypatch: pytest.MonkeyPatch):
        import cosctl.main as main_module

        class DeleteFailed(Exception):
            pass

        class FailingDeleteClient:
            def delete_object(self, **kwargs):
                raise DeleteFailed("delete failed")

        monkeypatch.setattr(main_module, "CosClientError", DeleteFailed)
        monkeypatch.setattr(
            main_module,
            "create_client_and_bucket",
            lambda: (FailingDeleteClient(), "bucket"),
        )

        result = runner.invoke(app, ["delete", "remote-file.txt"])

        assert result.exit_code == 1
        assert "deleted successfully" not in result.stdout
        assert "delete failed" in result.stderr
