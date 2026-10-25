#!/bin/sh
# One entry point for CI and local checks: scripts/ci.sh {test|hardware|lint|typecheck|all}
set -eu

cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/.."

sync() { uv sync --locked --group dev; }

case "${1:-}" in
    test)      sync; uv run --locked pytest --durations=10 ;;
    # Real instruments; never part of `all`. Extra arguments go to pytest:
    #   scripts/ci.sh hardware --hardware-actuate -m 'not scope'
    hardware)  sync; shift; uv run --locked pytest tests/hardware --hardware "$@" ;;
    lint)      sync; uv run --locked ruff check .; uv run --locked ruff format --check . ;;
    typecheck) sync; uv run --locked mypy src tests ;;
    all)       sync
               uv run --locked pytest
               uv run --locked ruff check .
               uv run --locked ruff format --check .
               uv run --locked mypy src tests ;;
    *)         echo "usage: $0 {test|hardware|lint|typecheck|all}" >&2; exit 2 ;;
esac
