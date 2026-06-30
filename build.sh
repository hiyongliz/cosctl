#!/bin/bash
set -euo pipefail

current_version=$(python - <<'PY'
from pathlib import Path

for line in Path('pyproject.toml').read_text().splitlines():
    if line.startswith('version = '):
        print(line.split('"')[1])
        break
PY
)

build_timestamp=$(date +"%Y%m%d.%H%M%S")
git_hash=$(git rev-parse --short HEAD)
dynamic_version="${current_version}+${build_timestamp}.${git_hash}"
backup_pyproject=$(mktemp)

cleanup() {
    cp "$backup_pyproject" pyproject.toml
    rm -f "$backup_pyproject"
}
trap cleanup EXIT

cp pyproject.toml "$backup_pyproject"

mkdir -p dist
rm -f dist/*.whl dist/*.tar.gz

echo "Building version: $dynamic_version"

python - <<'PY' "$dynamic_version"
from pathlib import Path
import sys

new_version = sys.argv[1]
pyproject_path = Path('pyproject.toml')
lines = pyproject_path.read_text().splitlines()
updated_lines = []
replaced = False

for line in lines:
    if not replaced and line.startswith('version = '):
        updated_lines.append(f'version = "{new_version}"')
        replaced = True
        continue
    updated_lines.append(line)

if not replaced:
    raise SystemExit('version field not found in pyproject.toml')

pyproject_path.write_text("\n".join(updated_lines) + "\n")
PY

uv run python -m build
