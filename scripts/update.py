#!/usr/bin/env python3
"""Update Python dependencies and versioned CI references.

The script only mutates the working tree. Validation and committing the
result belong to the developer invoking the update command.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
GITHUB_API = "https://api.github.com"
USER_AGENT = "iyzee-dependency-updater"

_RELEASE_CACHE: dict[str, str] = {}
_COMMIT_CACHE: dict[tuple[str, str], str] = {}


def get_json(url: str) -> object:
    headers = {"User-Agent": USER_AGENT}
    if url.startswith(GITHUB_API):
        headers["Accept"] = "application/vnd.github+json"

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def latest_release_tag(repository: str) -> str:
    if repository in _RELEASE_CACHE:
        return _RELEASE_CACHE[repository]

    data = get_json(f"{GITHUB_API}/repos/{repository}/releases/latest")
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise RuntimeError(f"{repository} has no usable latest release")

    tag = data["tag_name"]
    _RELEASE_CACHE[repository] = tag
    return tag


def latest_action_commit(repository: str, tag: str) -> str:
    key = (repository, tag)
    if key in _COMMIT_CACHE:
        return _COMMIT_CACHE[key]

    data = get_json(f"{GITHUB_API}/repos/{repository}/commits/{tag}")
    if not isinstance(data, dict) or not isinstance(data.get("sha"), str):
        raise RuntimeError(f"could not resolve {repository}@{tag} to a commit")

    sha = data["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"invalid commit SHA for {repository}@{tag}: {sha}")

    _COMMIT_CACHE[key] = sha
    return sha


def normalize_action_tag(tag: str) -> str:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,2})", tag)
    if match is None:
        raise RuntimeError(f"latest release is not a version tag: {tag}")
    return f"v{match.group(1)}"


def update_github_actions() -> bool:
    changed = False
    pattern = re.compile(
        r"(?P<prefix>\buses:\s*[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@)"
        r"(?P<ref>[^\s#]+)"
        r"(?P<comment>\s+#\s*(?P<comment_version>v?\d+(?:\.\d+){1,2}))?"
    )

    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        original = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            nonlocal changed

            prefix = match.group("prefix")
            ref = match.group("ref")
            comment = match.group("comment") or ""
            repository = prefix.split("uses:", 1)[1].strip().split("@", 1)[0]

            if ref.startswith("./"):
                return match.group(0)

            latest_tag = normalize_action_tag(latest_release_tag(repository))

            if re.fullmatch(r"[0-9a-f]{40}", ref):
                if not match.group("comment_version"):
                    return match.group(0)

                latest_sha = latest_action_commit(repository, latest_tag)
                latest_comment = f" # {latest_tag}"
                if ref == latest_sha and comment == latest_comment:
                    return match.group(0)

                changed = True
                return f"{prefix}{latest_sha}{latest_comment}"

            current_match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", ref)
            if current_match is None:
                return match.group(0)

            precision = 1 + sum(group is not None for group in current_match.groups()[1:])
            latest_parts = latest_tag.lstrip("v").split(".")
            current_parts = current_match.group(0).lstrip("v").split(".")

            if precision == 1:
                replacement_ref = f"v{latest_parts[0]}"
            elif precision == 2:
                replacement_ref = f"v{latest_parts[0]}.{latest_parts[1]}"
            else:
                replacement_ref = latest_tag

            if current_parts[:precision] == replacement_ref.lstrip("v").split("."):
                return match.group(0)

            changed = True
            return f"{prefix}{replacement_ref}{comment}"

        updated = pattern.sub(replace, original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")

    return changed


def update_tool_versions() -> bool:
    uv_version = latest_release_tag("astral-sh/uv").lstrip("v")
    typst_version = latest_release_tag("typst/typst").lstrip("v")
    changed = False

    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        original = path.read_text(encoding="utf-8")
        updated = re.sub(
            r"""(uses:\s*astral-sh/setup-uv@[^\n]+\n\s+with:\n\s+version:\s*)(['"])[^'"]+\2""",
            lambda match: f'{match.group(1)}"{uv_version}"',
            original,
        )
        updated = re.sub(
            r"""(typst-version:\s*)(['"])[^'"]+\2""",
            lambda match: f'{match.group(1)}"{typst_version}"',
            updated,
        )

        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed = True

    gitlab = ROOT / ".gitlab-ci.yml"
    original = gitlab.read_text(encoding="utf-8")
    updated = re.sub(
        r"(ghcr\.io/astral-sh/uv:)(\d+\.\d+\.\d+)(-python3\.14-trixie)",
        r"\g<1>" + uv_version + r"\g<3>",
        original,
    )
    updated = re.sub(
        r"(ghcr\.io/typst/typst:)\d+\.\d+\.\d+",
        r"\g<1>" + typst_version,
        updated,
    )
    if updated != original:
        gitlab.write_text(updated, encoding="utf-8")
        changed = True

    return changed


def update_python_lock() -> None:
    subprocess.run(["uv", "lock", "--upgrade"], cwd=ROOT, check=True)


def main() -> None:
    update_python_lock()
    update_github_actions()
    update_tool_versions()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
