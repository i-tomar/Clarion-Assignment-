#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
fi
exec "$PY" -m pytest tests -q
