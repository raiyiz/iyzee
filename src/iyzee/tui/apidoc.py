"""What an instrument offers, as text: ``lab.api(...)`` in the console.

Built by introspection, so it is never stale and works for every driver. A
method carrying a ``rest`` attribute (see ``devices.wavemeter.endpoint``) also
shows the HTTP route it calls.
"""

from __future__ import annotations

import inspect
from typing import Any

from ..devices.handles import LockedProxy


def _unwrap(target: Any) -> Any:
    """The device behind a ``LockedProxy``; anything else as is."""
    if isinstance(target, LockedProxy):
        return object.__getattribute__(target, "_target")
    return target


def _summary(member: Any) -> str:
    doc = inspect.getdoc(member)
    return doc.strip().splitlines()[0] if doc else ""


def describe_api(target: Any, pattern: str | None = None) -> str:
    """Public methods of ``target``: signature, route (if any), first doc line.

    ``pattern`` keeps only methods whose name or summary contains it
    (case-insensitive), e.g. ``describe_api(scope, "trig")``.
    """
    obj = _unwrap(target)
    needle = pattern.lower() if pattern else None
    methods: list[str] = []
    attributes: list[str] = []
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            member = getattr(obj, name)
        except Exception:  # noqa: BLE001 - a broken property must not hide the rest
            continue
        if not callable(member):
            attributes.append(name)
            continue
        summary = _summary(member)
        if needle and needle not in name.lower() and needle not in summary.lower():
            continue
        try:
            signature = str(inspect.signature(member))
        except TypeError, ValueError:
            signature = "(...)"
        route = getattr(member, "rest", None)
        head = f"  {name}{signature}" + (f"   [{route}]" if route else "")
        methods.append(head + (f"\n      {summary}" if summary else ""))

    lines = [repr(obj) if type(obj).__repr__ is not object.__repr__ else type(obj).__name__]
    lines += methods or [f"  (no methods match {pattern!r})" if pattern else "  (no methods)"]
    if attributes and not needle:
        lines.append("  attributes: " + ", ".join(attributes))
    return "\n".join(lines)
