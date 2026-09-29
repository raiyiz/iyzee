#!/usr/bin/env python3
"""Update Python dependencies and versioned CI references.

The script only mutates the working tree. Validation, commits and pushes belong
to the workflow that calls it.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
GITHUB_API = "https://api.github.com"
USER_AGENT = "iyzee-dependency-updater"


def get_json(url: str) -> object | None:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError):
        return None


def latest_release_tag(repository: str) -> str | None:
    data = get_json(f"{GITHUB_API}/repos/{repository}/releases/latest")
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    return tag if isinstance(tag, str) else None


def latest_action_commit(repository: str, tag: str) -> str | None:
    data = get_json(f"{GITHUB_API}/repos/{repository}/commits/{tag}")
    if not isinstance(data, dict):
        return None
    sha = data.get("sha")
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def normalize_action_tag(tag: str) -> str | None:
    if re.fullmatch(r"v?\d+(?:\.\d+){1,2}", tag):
        return tag if tag.startswith("v") else f"v{tag}"
    return None


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

            latest_tag = normalize_action_tag(latest_release_tag(repository) or "")
            if latest_tag is None:
                return match.group(0)

            if re.fullmatch(r"[0-9a-f]{40}", ref):
                if not match.group("comment_version"):
                    return match.group(0)

                latest_sha = latest_action_commit(repository, latest_tag)
                if latest_sha is None:
                    return match.group(0)

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


def registry_tags(image: str) -> list[str]:
    url = f"https://ghcr.io/v2/{image.removeprefix('ghcr.io/')}/tags/list?n=1000"
    data = get_json(url)
    if isinstance(data, dict):
        tags = data.get("tags")
        if isinstance(tags, list):
            return [tag for tag in tags if isinstance(tag, str)]
    return []


def version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def latest_matching_docker_tag(current_tag: str, tags: list[str]) -> str | None:
    if version_tuple(current_tag) is not None:
        candidates = [
            (version_tuple(tag), tag)
            for tag in tags
            if version_tuple(tag) is not None
        ]
        return max(candidates, key=lambda item: item[0] or (0, 0, 0))[1] if candidates else None

    suffix_match = re.fullmatch(
        r"(\d+\.\d+\.\d+)(-python3\.14-trixie)",
        current_tag,
    )
    if suffix_match is None:
        return None

    suffix = suffix_match.group(2)
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for tag in tags:
        match = re.fullmatch(r"(\d+\.\d+\.\d+)" + re.escape(suffix), tag)
        if match is None:
            continue
        version = version_tuple(match.group(1))
        if version is not None:
            candidates.append((version, tag))

    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def update_ghcr_images() -> bool:
    changed = False
    pattern = re.compile(
        r"(?P<prefix>ghcr\.io/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+:)"
        r"(?P<tag>[^\s#]+)"
    )

    paths = [ROOT / ".gitlab-ci.yml", *sorted(WORKFLOW_DIR.glob("*.y*ml"))]
    cache: dict[str, list[str]] = {}

    for path in paths:
        original = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            nonlocal changed

            image = match.group("prefix")[:-1]
            current_tag = match.group("tag")
            tags = cache.setdefault(image, registry_tags(image))
            latest_tag = latest_matching_docker_tag(current_tag, tags)

            if latest_tag is None or latest_tag == current_tag:
                return match.group(0)

            changed = True
            return f"{match.group('prefix')}{latest_tag}"

        updated = pattern.sub(replace, original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")

    return changed


def update_python_lock() -> None:
    subprocess.run(["uv", "lock", "--upgrade"], cwd=ROOT, check=True)


def main() -> None:
    update_python_lock()
    update_github_actions()
    update_ghcr_images()


if __name__ == "__main__":
    main()
