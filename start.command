#!/bin/zsh
set -eu
cd "$(dirname "$0")"
exec uv run uvicorn studio.app:app --host 127.0.0.1 --port 8790 --no-access-log
