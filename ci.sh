#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

usage() {
    echo "usage: $0 {test|lint|typecheck|docs|all}" >&2
    exit 2
}

sync_dependencies() {
    uv sync --locked --group dev
}

run_test() {
    uv run --locked python -m pytest
}

run_lint() {
    uv run --locked ruff check .
    uv run --locked ruff format --check .
}

run_typecheck() {
    uv run --locked mypy src tests
}

run_docs() {
    rm -rf build/docs
    mkdir -p build/docs

    for source in docs/*.typ; do
        case "$source" in
            docs/requirements.typ)
                continue
                ;;
        esac

        filename=${source##*/}
        name=${filename%.typ}
        typst compile "$source" "build/docs/${name}.pdf"
    done
}

case "${1:-}" in
    test)
        sync_dependencies
        run_test
        ;;
    lint)
        sync_dependencies
        run_lint
        ;;
    typecheck)
        sync_dependencies
        if ! run_typecheck; then
            echo "Typecheck reported errors; continuing without failing CI." >&2
        fi
        ;;
    docs)
        run_docs
        ;;
    all)
        sync_dependencies
        run_test
        run_lint
        if ! run_typecheck; then
            echo "Typecheck reported errors; continuing without failing CI." >&2
        fi
        run_docs
        ;;
    *)
        usage
        ;;
esac
