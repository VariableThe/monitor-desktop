#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -x "$project_dir/.venv/bin/python" ]; then
  printf '%s\n' "Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi
exec "$project_dir/.venv/bin/python" "$project_dir/run.py"
