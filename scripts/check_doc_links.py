#!/usr/bin/env python3
"""Check that the documentation and the code still refer to things that exist.

Guide -> code
    Every ``#code("Name")``, ``#anchors("A", "B")`` and ``#tested-by("test_x")`` in
    ``docs/chapters/*.typ`` must resolve to exactly one symbol under ``src/iyzee`` or
    ``tests`` (a dotted suffix such as ``Lab.connect`` is enough; an ambiguous name must
    be qualified). ``#file("path")`` and ``#source("path", ...)`` must name a file that
    exists.
Code -> guide
    Every ``:guide:`label``` in a module docstring must name a ``<label>`` that exists in
    the guide.
README
    Every relative link must point at an existing file or directory.

Names resolve through ``scripts/docs_index.py``, the same index the guide is built from,
so this check and the Typst build cannot disagree. It needs no Typst. Line numbers are
never written by hand anywhere, so there is nothing to "fix": a failure means a rename or
a removal that the docs have not caught up with.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docs_index  # noqa: E402

FILE_REFS = re.compile(r'#(?:file|source)\(\s*"([^"]+)"')
README_LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s#]+)(?:#[^)\s]*)?\)")


def main() -> int:
    index, problems = docs_index.build()

    for path in docs_index.typst_files():
        if path.name == "requirements.typ":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for target in FILE_REFS.findall(line):
                if not (ROOT / target).exists():
                    problems.append(f"{path.relative_to(ROOT)}:{number}: missing file {target}")

    readme = ROOT / "README.md"
    for number, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        for target in README_LINK.findall(line):
            if not (ROOT / target).exists():
                problems.append(f"README.md:{number}: broken link {target}")

    if problems:
        print(f"Found {len(problems)} broken documentation reference(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    cited = len(index["xref"]["refs"])
    print(f"All documentation references are valid ({cited} code symbols cited by the guide).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
