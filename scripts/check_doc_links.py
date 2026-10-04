#!/usr/bin/env python3
"""Check source-code links in README.md and docs/*.typ.

Supported forms:
  #src-link("src/iyzee/foo.py", line: N)
  [\x60Symbol\x60](src/iyzee/foo.py#L123)

Files must exist; anchored lines must be class/def/async-def declarations.
Named symbols must match. With --fix, uniquely recoverable stale lines are
updated automatically.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEF = re.compile(r"^\s*(?:class|(?:async\s+)?def)\s+([A-Za-z_]\w*)\b")
TYPST = re.compile(r'#src-link\(\s*"([^"]+)"([^)]*)\)')
README = re.compile(r"\[\x60([^\x60]+)\x60\]\((src/[^)#\s]+)(?:#L(\d+))?\)")
LINE = re.compile(r"\bline\s*:\s*(\d+)")


def name(label: str) -> str | None:
    candidate = label.strip().rstrip("()").rsplit(".", 1)[-1]
    return candidate if re.fullmatch(r"[A-Za-z_]\w*", candidate) else None


def typst_name(text: str, start: int) -> str | None:
    before = text[text.rfind("\n", 0, start) + 1 : start]
    for label in reversed(re.findall(r"\x60([A-Za-z_][\w.]*(?:\(\))?)\x60", before)):
        if not label.endswith(".py") and (candidate := name(label)):
            return candidate
    return None


def links(text: str, typst: bool):
    for match in (TYPST if typst else README).finditer(text):
        if typst:
            path, rest = match.groups()
            line = LINE.search(rest)
            yield match, path, int(line.group(1)) if line else None, (
                typst_name(text, match.start()) if line else None
            )
        else:
            label, path, line = match.groups()
            yield match, path, int(line) if line else None, name(label) if line else None


def process(path: Path, typst: bool, fix: bool) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for match, target, line, symbol in links(text, typst):
        source = ROOT / target
        if not source.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing {target}")
            continue
        if line is None:
            continue

        lines = source.read_text(encoding="utf-8").splitlines()
        if not 1 <= line <= len(lines):
            errors.append(f"{path.relative_to(ROOT)}: {target}#L{line} out of range")
            continue

        definitions = [
            n for n, source_line in enumerate(lines, 1)
            if (match_def := DEF.match(source_line))
            and symbol is not None
            and match_def.group(1) == symbol
        ]
        if fix and symbol is not None and len(definitions) == 1 and definitions[0] != line:
            new_line = definitions[0]
            anchor = r"(line\s*:\s*)\d+" if typst else r"#L\d+"
            replacement = re.sub(
                anchor,
                lambda m: f"{m.group(1)}{new_line}" if typst else f"#L{new_line}",
                match.group(0),
                count=1,
            )
            replacements.append((match.start(), match.end(), replacement))
            continue

        match_def = DEF.match(lines[line - 1])
        if match_def is None:
            errors.append(f"{path.relative_to(ROOT)}: {target}#L{line} is not a definition")
        elif symbol is not None and match_def.group(1) != symbol:
            errors.append(
                f"{path.relative_to(ROOT)}: {target}#L{line} defines "
                f"{match_def.group(1)!r}, expected {symbol!r}"
            )

    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    if fix and replacements:
        path.write_text(text, encoding="utf-8")
    return errors


fix = "--fix" in sys.argv[1:]
errors = [
    error
    for doc in sorted((ROOT / "docs").glob("*.typ"))
    for error in process(doc, True, fix)
]
errors.extend(process(ROOT / "README.md", False, fix))

if errors:
    print(f"Found {len(errors)} broken/stale source link(s):", file=sys.stderr)
    print("\n".join(f"  - {error}" for error in errors), file=sys.stderr)
    raise SystemExit(1)

print("All documentation source links are valid.")
