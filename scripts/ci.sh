#!/bin/sh
# One entry point for CI and local checks: scripts/ci.sh {test|lint|typecheck|doc-links|docs-index|docs|all|update}
set -eu

cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/.."

sync() { uv sync --locked --group dev; }

# The guide asks the code for names, line numbers, defaults and the on-disk schema;
# this writes that index (docs/code-index.json, not committed).
docs_index() {
    uv run --locked scripts/docs_index.py
}

docs() {
    # DOCS_INDEX_PREBUILT=1: the index was produced by an earlier job (GitLab builds it
    # in a Python image and compiles in the Typst image).
    if [ "${DOCS_INDEX_PREBUILT:-}" != 1 ]; then
        sync
        docs_index
    fi
    rm -rf build/docs
    mkdir -p build/docs
    # Every code link in the PDF points at the commit it was built from.
    ref="${DOCS_REF:-${GITHUB_SHA:-${CI_COMMIT_SHA:-$(git rev-parse HEAD 2>/dev/null || echo main)}}}"
    # One book: docs/main.typ includes every chapter in docs/chapters/.
    typst compile --root . --input "ref=$ref" docs/main.typ build/docs/iyzee-guide.pdf
}

doc_links() {
    uv run --locked scripts/check_doc_links.py
}

all() {
    sync
    uv run --locked pytest
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked mypy src tests
    uv run --locked scripts/check_doc_links.py
    docs
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
    all

    echo
    echo "Dependency update validation passed."
    echo "Branch: $branch"
    echo "Review changes with: git diff main...HEAD"
}

case "${1:-}" in
    test)      sync; uv run --locked pytest --durations=25 ;;
    lint)      sync; uv run --locked ruff check .; uv run --locked ruff format --check . ;;
    typecheck) sync; uv run --locked mypy src tests ;;
    doc-links) sync; doc_links ;;
    docs-index) sync; docs_index ;;
    docs)      docs ;;
    all)       all ;;
    update)    update ;;
    *)         echo "usage: $0 {test|lint|typecheck|doc-links|docs-index|docs|all|update}" >&2; exit 2 ;;
esac
