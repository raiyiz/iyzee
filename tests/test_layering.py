"""The layering the Architecture part describes, enforced on the real import graph.

Lower layers must not know about higher ones: drivers do not know experiments, and
nothing below ``tui`` imports Textual. ``scripts/docs_index.py`` draws the same graph
in the guide.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _graph():
    spec = importlib.util.spec_from_file_location("docs_index", ROOT / "scripts" / "docs_index.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["docs_index"] = module
    spec.loader.exec_module(module)
    index, _ = module.build()
    return index


INDEX = _graph()
LAYER = {name: record["layer"] for name, record in INDEX["modules"].items()}
ALLOWED = {
    "config": set(),
    "devices": {"config"},
    "experiment": {"config", "devices"},
    "session": {"config", "devices", "experiment"},
    "tui": {"config", "devices", "experiment", "session"},
    "entry": {"config", "devices", "experiment", "session", "tui"},
}


def test_every_internal_import_points_down_the_layers() -> None:
    violations = []
    for module, deps in INDEX["imports"].items():
        mine = LAYER[module]
        for dep in deps:
            theirs = LAYER[dep]
            if theirs != mine and theirs not in ALLOWED[mine]:
                violations.append(f"{module} ({mine}) imports {dep} ({theirs})")
    assert not violations, "\n".join(violations)


def test_only_the_tui_imports_textual() -> None:
    offenders = []
    for module, record in INDEX["modules"].items():
        if record["layer"] in ("tui", "tests"):
            continue
        source = (ROOT / record["path"]).read_text(encoding="utf-8")
        if "import textual" in source or "from textual" in source:
            offenders.append(module)
    assert not offenders, offenders
