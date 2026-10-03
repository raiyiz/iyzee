#!/bin/sh
# One entry point for CI and local checks: scripts/ci.sh {test|lint|typecheck|docs|all|update}
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

update() {
    if [ "$(git branch --show-current)" != main ]; then
        echo "dependency updates must be started from main" >&2
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "working tree is not clean; refusing to create an update branch" >&2
        exit 1
    fi

    branch="automation/dependency-update-$(date -u +%Y%m%d-%H%M%S)"
    git switch -c "$branch"

    uv run scripts/update.py

    echo
    echo "Dependency update result:"
    if git diff --quiet; then
        echo "  no changes"
    else
        git diff --stat
    fi

    echo
    echo "Running full validation..."
    "$0" all

    echo
    echo "Dependency update validation passed."
    echo "Branch: $branch"
    echo "Review changes with: git diff main...HEAD"
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
    update)    update ;;
    *)         echo "usage: $0 {test|lint|typecheck|docs|all|update}" >&2; exit 2 ;;
esac
