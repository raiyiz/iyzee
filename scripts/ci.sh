#!/bin/sh
# One entry point for CI and local checks: scripts/ci.sh {test|lint|typecheck|docs|all}
set -eu

cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/.."

sync() { uv sync --locked --group dev; }

docs() {
    rm -rf build/docs
    mkdir -p build/docs
    for source in docs/*.typ; do
        name=${source##*/}
        # requirements.typ only declares Typst package imports.
        [ "$name" = requirements.typ ] && continue
        typst compile "$source" "build/docs/${name%.typ}.pdf"
    done
}

case "${1:-}" in
    test)      sync; uv run --locked pytest ;;
    lint)      sync; uv run --locked ruff check .; uv run --locked ruff format --check . ;;
    typecheck) sync; uv run --locked mypy src tests ;;
    docs)      docs ;;
    all)       sync
               uv run --locked pytest
               uv run --locked ruff check .
               uv run --locked ruff format --check .
               uv run --locked mypy src tests
               docs ;;
    *)         echo "usage: $0 {test|lint|typecheck|docs|all}" >&2; exit 2 ;;
esac
