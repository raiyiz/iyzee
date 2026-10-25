#!/bin/sh
# One entry point for CI and local checks: scripts/ci.sh {test|test-ui-core|test-ui-controls|test-ui-results|test-core-a|test-core-b|hardware|lint|typecheck|doc-links|notebooks|docs-index|docs|all|update}
set -eu

cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/.."

sync() { uv sync --locked --group dev; }

# CI test shards use this shared wrapper. Local `test` still runs the entire suite.
test_group() {
    sync
    uv run --locked pytest --durations=15 "$@"
}

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
    typst compile --root docs --input "ref=$ref" docs/main.typ build/docs/iyzee-guide.pdf
}

doc_links() {
    uv run --locked scripts/check_doc_links.py
}

notebooks() {
    uv sync --locked --group dev --group notebooks
    uv run --locked --group notebooks marimo check --strict notebooks/workbench.py notebooks/sweep_scope_analysis.py notebooks/reproduce_frequency_sweep.py
    uv run --locked --group notebooks pytest tests/test_notebook_toolbox.py tests/test_notebook_workset.py
}

all() {
    sync
    # Notebook tests run in notebooks() below, so don't execute them twice.
    uv run --locked pytest --ignore=tests/test_notebook_toolbox.py --ignore=tests/test_notebook_workset.py
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked mypy src tests
    uv run --locked scripts/check_doc_links.py
    notebooks
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
    test-ui-core)
        test_group tests/test_app.py tests/test_command_bar.py tests/test_connect_screen.py \
            tests/test_console.py tests/test_log_screen.py tests/test_rb_screen.py
        ;;
    test-ui-controls)
        test_group tests/test_power_screen.py tests/test_scope_screen.py tests/test_sweep_screen.py
        ;;
    test-ui-results)
        test_group tests/test_layout.py tests/test_results_screen.py tests/test_tui_plotting.py
        ;;
    test-core-a)
        test_group tests/test_analysis.py tests/test_base.py tests/test_commands.py \
            tests/test_config.py tests/test_diagnostics.py tests/test_docs_data.py \
            tests/test_docs_examples.py tests/test_docs_physics.py tests/test_experiment_core.py \
            tests/test_experiment_io.py tests/test_experiment_procedures.py \
            tests/test_hardware_gating.py tests/test_layering.py tests/hardware
        ;;
    test-core-b)
        test_group tests/test_handles.py tests/test_ipython.py tests/test_ipython_controller.py \
            tests/test_lab.py tests/test_mxa.py tests/test_plotting.py tests/test_power.py \
            tests/test_prefs.py tests/test_scope.py tests/test_scope_workflows.py \
            tests/test_waveform_math.py tests/test_wavemeter.py
        ;;
    # Real instruments; never part of `all`. Extra arguments go to pytest:
    #   scripts/ci.sh hardware --hardware-actuate -m 'not scope'
    hardware)  sync; shift; uv run --locked pytest tests/hardware --hardware "$@" ;;
    lint)      sync; uv run --locked ruff check .; uv run --locked ruff format --check . ;;
    typecheck) sync; uv run --locked mypy src tests ;;
    doc-links) sync; doc_links ;;
    notebooks)  notebooks ;;
    docs-index) sync; docs_index ;;
    docs)      docs ;;
    all)       all ;;
    update)    update ;;
    *)         echo "usage: $0 {test|test-ui-core|test-ui-controls|test-ui-results|test-core-a|test-core-b|hardware|lint|typecheck|doc-links|notebooks|docs-index|docs|all|update}" >&2; exit 2 ;;
esac
