# COS Cli

A simple command line tool for managing Tencent Cloud COS (Cloud Object Storage).

## Install

Requires Python 3.10 or newer.

```bash
pip install cosctl
```

For development dependencies:

```bash
pip install -e .[dev]
```

## Environment variables

```bash
export SECRET_ID=""
export SECRET_KEY=""
export BUCKET=""
export REGION="wuxi"
export DOMAIN=""
```

Required:
- `SECRET_ID`
- `SECRET_KEY`
- `BUCKET` (falls back to the first label of `DOMAIN` when not set, e.g. `my-bucket.cos.ap-beijing.myqcloud.com` yields `my-bucket`)

Optional:
- `REGION` (defaults to `wuxi`)
- `DOMAIN`

## Usage

```bash
cosctl --help
cosctl --version
cosctl list
cosctl upload ./local-file.txt remote-file.txt
cosctl get remote-file.txt ./downloaded.txt
cosctl delete remote-file.txt
```

## Build

构建时会自动在基础版本后追加动态后缀：`时间戳 + git 短哈希`。
例如：`0.1.1+20260402.123045.abc1234`

```bash
make sync
make build
```

## Test

```bash
pytest
```
