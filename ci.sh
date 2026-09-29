#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

usage() {
    echo "usage: $0 {test|lint|typecheck|docs|all}" >&2
    exit 2
}

case "\${1:-}" in
    test|lint|typecheck|all)
        uv sync --locked --group dev
        ;;
    docs)
        ;;
    *)
        usage
        ;;
esac

case "\${1:-}" in
    test)
        exec uv run --locked python -m pytest
        ;;
    lint)
        uv run --locked ruff check .
        uv run --locked ruff format --check .
        ;;
    typecheck)
        exec uv run --locked mypy src tests
        ;;
    docs)
        rm -rf build/docs
        mkdir -p build/docs

        for source in docs/*.typ; do
            case "$source" in
                docs/requirements.typ)
                    continue
                    ;;
            esac

            filename=\${source##*/}
            name=\${filename%.typ}
            typst compile "$source" "build/docs/\${name}.pdf"
        done
        ;;
    all)
        uv run --locked python -m pytest
        uv run --locked ruff check .
        uv run --locked ruff format --check .
        uv run --locked mypy src tests
        "$0" docs
        ;;
    *)
        usage
        ;;
esac
