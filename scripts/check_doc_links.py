#!/usr/bin/env python3
"""Check source-code links in README.md and docs/**/*.typ.

Supported forms:
  #src-link("src/iyzee/foo.py", line: N)
  [`Symbol`](src/iyzee/foo.py#L123)

Files must exist; anchored lines must be class/def/async-def declarations.
Named symbols must match. With --fix, stale line numbers that resolve to
exactly one definition in the target file are rewritten automatically.
The symbol is taken from the nearest backtick label, which may sit on the
previous line.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEF = re.compile(r"^\s*(?:class|(?:async\s+)?def)\s+([A-Za-z_]\w*)\b")
TYPST = re.compile(r'#src-link\(\s*"([^"]+)"([^)]*)\)')
README = re.compile(r"\[\x60([^\x60]+)\x60\]\((src/[^)#\s]+)(?:#L(\d+))?\)")
LINE = re.compile(r"\bline\s*:\s*(\d+)")
LABEL = re.compile(r"\x60([A-Za-z_][\w.]*(?:\(\))?)\x60")


def name(label: str) -> str | None:
    candidate = label.strip().rstrip("()").rsplit(".", 1)[-1]
    return candidate if re.fullmatch(r"[A-Za-z_]\w*", candidate) else None


def typst_name(text: str, start: int, lines_back: int = 2) -> str | None:
    """Nearest symbol-looking backtick label before the link: same line, or
    up to `lines_back - 1` lines above it, scanned right to left."""
    cut = start
    for _ in range(lines_back):
        cut = text.rfind("\n", 0, cut)
    for label in reversed(LABEL.findall(text[cut + 1 : start])):
        if not label.endswith(".py") and (candidate := name(label)):
            return candidate
    return None


def links(text: str, typst: bool):
    for match in (TYPST if typst else README).finditer(text):
        if typst:
            path, rest = match.groups()
            line = LINE.search(rest)
            yield (
                match,
                path,
                int(line.group(1)) if line else None,
                (typst_name(text, match.start()) if line else None),
            )
        else:
            label, path, line = match.groups()
            yield match, path, int(line) if line else None, name(label) if line else None


def process(path: Path, typst: bool, fix: bool) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    fixes: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    for match, target, line, symbol in links(text, typst):
        source = ROOT / target
        if not source.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing {target}")
            continue
        if line is None:
            continue

        lines = source.read_text(encoding="utf-8").splitlines()
        definitions: list[int] = []
        if symbol is not None:
            definitions = [
                n
                for n, source_line in enumerate(lines, 1)
                if (match_def := DEF.match(source_line)) and match_def.group(1) == symbol
            ]

        if fix and len(definitions) == 1 and definitions[0] != line:
            new_line = definitions[0]
            anchor = r"(line\s*:\s*)\d+" if typst else r"#L\d+"
            replacement = re.sub(
                anchor,
                lambda m: f"{m.group(1)}{new_line}" if typst else f"#L{new_line}",
                match.group(0),
                count=1,
            )
            replacements.append((match.start(), match.end(), replacement))
            fixes.append(f"{path.relative_to(ROOT)}: {target}#L{line} -> #L{new_line} ({symbol})")
            continue

        if not 1 <= line <= len(lines):
            errors.append(f"{path.relative_to(ROOT)}: {target}#L{line} out of range")
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
    return errors, fixes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check source-code links in README.md and docs/**/*.typ."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite stale line numbers that resolve to exactly one definition",
    )
    args = parser.parse_args()

    errors: list[str] = []
    fixes: list[str] = []
    documents = sorted((ROOT / "docs").rglob("*.typ")) + [ROOT / "README.md"]
    for document, is_typst in [(d, True) for d in documents[:-1]] + [(documents[-1], False)]:
        doc_errors, doc_fixes = process(document, is_typst, args.fix)
        errors.extend(doc_errors)
        fixes.extend(doc_fixes)

    for fixed in fixes:
        print(f"fixed: {fixed}", file=sys.stderr)

    if errors:
        print(f"Found {len(errors)} broken/stale source link(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("All documentation source links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
