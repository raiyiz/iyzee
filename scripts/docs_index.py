#!/usr/bin/env python3
"""Build ``docs/code-index.json``: the bridge between the guide and the code.

The guide never hard-codes a line number, a default value, a file layout or a
manifest field. It *asks* this index, and the index is computed from the code:

``symbols``
    Every module, class, function, method and module/class-level constant under
    ``src/iyzee`` and ``tests`` with its file, line range and the first line of
    its docstring. ``#code("Lab.connect")`` in a chapter resolves through it, so
    the link text, the GitHub line range and the one-line summary shown in the
    guide are the code's own. A name that no longer exists is a build error.
``lookup``
    Every dotted suffix of every symbol, so ``Lab.connect`` or even ``connect``
    (when unique) resolves without the full ``iyzee.lab.`` prefix.
``facts``
    Values read from the *imported* code: instrument addresses, the standard
    sweep presets, timeouts, key bindings, pages, enum values. Prose says
    ``#fact("sweeps.bandwidth.avg_count")`` instead of repeating ``200``.
``recordings``
    The real on-disk schema, produced by running the real save functions on a
    tiny synthetic run and reading the files back. The data dictionary in the
    guide is rendered from it, and :data:`FIELD_DOCS` must describe exactly the
    fields that exist: adding a manifest field without documenting it (or
    documenting one that is gone) fails the build.
``imports``
    The internal import graph, which the guide draws and
    ``tests/test_layering.py`` enforces.
``xref``
    The reverse direction: which guide sections cite which symbols, and which
    module docstrings point at which guide labels (``:guide:`label```).

Usage::

    uv run scripts/docs_index.py            # (re)write docs/code-index.json
    uv run scripts/docs_index.py --check    # fail if the committed file is stale

The generated file is not committed (it changes with every edit that moves a
line); ``scripts/ci.sh docs`` writes it before compiling the guide.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUTPUT = ROOT / "docs" / "code-index.json"
DOCS = ROOT / "docs"
sys.path.insert(0, str(SRC))

GUIDE_ROLE = re.compile(r":guide:`([A-Za-z0-9_-]+)`")
SYM_CALL = re.compile(r"#(?:code|anchors|tested-by)\(([^)]*)\)")
QUOTED = re.compile(r'"([^"]+)"')
HEADING = re.compile(r"^(=+)\s+(.*?)\s*(?:<([A-Za-z0-9_-]+)>)?\s*$")
PART_ID = re.compile(r'\bid:\s*"([A-Za-z][A-Za-z0-9_-]*)"')
STANDALONE_LABEL = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9_-]*)>\s*$")

# ---------------------------------------------------------------------------
# 1. symbols
# ---------------------------------------------------------------------------


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC if SRC in path.parents else ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


_ROLE = re.compile(r":[a-z]+:`~?([^`]+)`")
_SENTENCE_END = re.compile(r"(?<=[A-Za-z0-9)\]`])\.(?:\s|$)")


def _first_line(doc: str | None) -> str:
    """The first sentence of a docstring, as plain text (RST markup removed)."""
    if not doc:
        return ""
    paragraph = " ".join(doc.strip().split("\n\n")[0].split())
    paragraph = _ROLE.sub(r"\1", paragraph).replace("``", "").replace("`", "")
    paragraph = re.sub(r"(?<!\w)\*([^*\s][^*]*?)\*(?!\w)", r"\1", paragraph)
    match = _SENTENCE_END.search(paragraph)
    sentence = paragraph[: match.start()] if match else paragraph.rstrip(".")
    return sentence if len(sentence) <= 170 else sentence[:167].rstrip() + "..."


def _arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    names = [a.arg for a in (*args.posonlyargs, *args.args)]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        names.append("*")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return ", ".join(n for n in names if n not in ("self", "cls"))


def _start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)])  # type: ignore[attr-defined]


def _targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    raw = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in raw if isinstance(t, ast.Name)]


def index_file(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Symbols and the module record for one Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    rel = path.relative_to(ROOT).as_posix()
    docstring = ast.get_docstring(tree)
    symbols: dict[str, Any] = {}

    def add(qualified: str, kind: str, node: ast.AST, summary: str = "", sig: str = "") -> None:
        symbols[qualified] = {
            "kind": kind,
            "path": rel,
            "start": _start(node),
            "end": getattr(node, "end_lineno", None) or _start(node),
            "summary": summary,
            "sig": sig,
        }

    def walk(body: list[ast.stmt], prefix: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                add(f"{prefix}.{node.name}", "class", node, _first_line(ast.get_docstring(node)))
                walk(node.body, f"{prefix}.{node.name}", True)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                kind = "method" if in_class else "function"
                if node.name.startswith("test_") and module.startswith("tests"):
                    kind = "test"
                add(
                    f"{prefix}.{node.name}",
                    kind,
                    node,
                    _first_line(ast.get_docstring(node)),
                    _arg_names(node),
                )
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                for name in _targets(node):
                    if name.startswith("__") or name in ("log", "T", "P"):
                        continue
                    add(f"{prefix}.{name}", "attribute" if in_class else "constant", node)

    walk(tree.body, module, False)
    record = {
        "path": rel,
        "summary": _first_line(docstring),
        "lines": len(path.read_text(encoding="utf-8").splitlines()),
        "guide": sorted(set(GUIDE_ROLE.findall(docstring or ""))),
        "layer": "tests" if module.startswith("tests") else _layer(module),
    }
    symbols[module] = {
        "kind": "module",
        "path": rel,
        "start": 1,
        "end": record["lines"],
        "summary": record["summary"],
        "sig": "",
    }
    return symbols, record


def _layer(module: str) -> str:
    """Which architectural layer a module belongs to (see the Architecture part)."""
    if module == "iyzee.config":
        return "config"
    if module.startswith("iyzee.devices"):
        return "devices"
    if module.startswith("iyzee.experiment") or module in ("iyzee.waveform_math", "iyzee.analysis"):
        return "experiment"
    if module in ("iyzee.lab", "iyzee.scope_workflows"):
        return "session"
    if module.startswith("iyzee.tui"):
        return "tui"
    return "entry"


def python_files() -> list[Path]:
    files = sorted((SRC / "iyzee").rglob("*.py"))
    files += sorted((ROOT / "tests").glob("*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


def internal_imports(path: Path) -> set[str]:
    """Modules of this package that ``path`` imports (anywhere, including lazily)."""
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            if target.split(".")[0] == "iyzee":
                found.add(target)
                found.update(f"{target}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "iyzee")
    return found


# ---------------------------------------------------------------------------
# 2. lookup, xref
# ---------------------------------------------------------------------------


def build_lookup(symbols: dict[str, Any]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for qualified in symbols:
        parts = qualified.split(".")
        for i in range(len(parts)):
            lookup.setdefault(".".join(parts[i:]), []).append(qualified)
    return lookup


def resolve(lookup: dict[str, list[str]], name: str) -> list[str]:
    """Candidates for ``name``; exactly one means the reference is valid."""
    candidates = lookup.get(name, [])
    if len(candidates) > 1:
        # A source symbol beats a same-named test; a module beats its members' names.
        source = [c for c in candidates if not c.startswith("tests.")]
        if len(source) == 1:
            return source
    return candidates


def typst_files() -> list[Path]:
    return sorted((DOCS / "chapters").glob("*.typ"))


def scan_typst(lookup: dict[str, list[str]]) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Which headings cite which symbols; every label's heading; unresolved names."""
    refs: dict[str, list[dict[str, str]]] = {}
    labels: dict[str, str] = {}
    problems: list[str] = []
    for path in typst_files():
        if path.name in ("code-map.typ", "requirements.typ"):
            continue
        chapter = path.stem
        heading, heading_label = chapter, ""
        in_fence = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            fences = line.count("```")
            if fences % 2:  # an odd count opens or closes a raw block (even closes on its own line)
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            found = HEADING.match(line)
            if found:
                heading = found.group(2).replace("`", "")
                heading_label = found.group(3) or ""
                if heading_label:
                    labels[heading_label] = f"{chapter}: {heading}"
            for label in (*PART_ID.findall(line), *STANDALONE_LABEL.findall(line)):
                labels.setdefault(label, f"{chapter}: {heading}")
            for call in SYM_CALL.finditer(line):
                for name in QUOTED.findall(call.group(1)):
                    if "/" in name:  # a #file(...) style path, not a symbol
                        continue
                    hits = resolve(lookup, name)
                    if len(hits) != 1:
                        problems.append(
                            f"{path.relative_to(ROOT)}:{number}: "
                            + (
                                f"unknown symbol {name!r}"
                                if not hits
                                else f"ambiguous symbol {name!r}: {', '.join(hits)}"
                            )
                        )
                        continue
                    refs.setdefault(hits[0], []).append(
                        {"chapter": chapter, "heading": heading, "label": heading_label}
                    )
    return refs, labels, problems


# ---------------------------------------------------------------------------
# 3. facts (read from the imported code)
# ---------------------------------------------------------------------------


def build_facts() -> dict[str, Any]:
    from textual.binding import Binding

    from iyzee import config
    from iyzee.devices import scope, wavemeter
    from iyzee.devices.handles import ScopeHandle
    from iyzee.devices.mxa import KeysightMXA
    from iyzee.experiment import io, procedures
    from iyzee.lab import INSTRUMENTS
    from iyzee.tui import app
    from iyzee.tui.screens import sweep

    def bindings() -> list[dict[str, Any]]:
        return [
            {
                "key": b.key,
                "action": b.action,
                "description": b.description,
                "priority": b.priority,
                "show": b.show,
            }
            for b in app.IyzeeApp.BINDINGS
            if isinstance(b, Binding)
        ]

    def steps(items: list[Any]) -> dict[str, Any]:
        return {"count": len(items), "first": items[0].label, "last": items[-1].label}

    freq_steps = procedures.frequency_sweep_steps()
    return {
        "addresses": {
            member.name.lower(): {
                "default": member.value,
                "env": f"IYZEE_{member.name}_IP",
            }
            for member in config.IP
        },
        "config": {
            "env_data_dir": "IYZEE_DATA_DIR",
            "env_config_file": "IYZEE_CONFIG",
        },
        "instruments": [{"key": s.key, "label": s.label, "short": s.short} for s in INSTRUMENTS],
        "pages": [
            {"id": page_id, "label": label, "screen": cls.__name__}
            for page_id, label, cls in app.PAGE_SPECS
        ],
        "bindings": bindings(),
        "tui": {
            "link_check_interval_s": app.IyzeeApp.LINK_CHECK_INTERVAL,
            "quit_confirm_s": app.IyzeeApp.QUIT_CONFIRM_S,
            "command_palette": app.IyzeeApp.COMMAND_PALETTE_BINDING,
            "breakpoints": [list(b) for b in app.IyzeeApp.HORIZONTAL_BREAKPOINTS],
            "max_points": sweep.MAX_POINTS,
            "live_plot_interval_s": sweep.LIVE_PLOT_INTERVAL_S,
        },
        "sweeps": {
            "analyzer_defaults": asdict(procedures.AnalyzerConfig()),
            "bandwidth": asdict(procedures.bandwidth_sweep_config()),
            "frequency": asdict(procedures.frequency_sweep_config()),
            "bandwidth_steps": {
                **steps(procedures.bandwidth_sweep_steps()),
                "rbw_hz": [s.rbw_hz for s in procedures.bandwidth_sweep_steps()],
            },
            "frequency_steps": {
                **steps(freq_steps),
                "center_thz": _default(procedures.frequency_sweep_steps, "laser_center_thz"),
                "wavemeter_channel": _default(
                    procedures.frequency_sweep_steps, "wavemeter_channel"
                ),
                "offsets_thz": [
                    round(
                        s.frequency_thz
                        - _default(procedures.frequency_sweep_steps, "laser_center_thz"),
                        12,
                    )
                    for s in freq_steps
                ],
            },
            "settle_time_s": procedures.SETTLE_TIME_S,
            "trace_squeezing": procedures.TRACE_SQZ,
            "trace_shot": procedures.TRACE_SHOT,
        },
        "drivers": {
            "mxa_timeout_ms": _default(KeysightMXA.__init__, "timeout_ms"),
            "scope_timeout_ms": scope.LeCroy.DEFAULT_TIMEOUT_MS,
            "scope_chunk_size": scope.LeCroy.CHUNK_SIZE,
            "scope_first_response_s": ScopeHandle.FIRST_RESPONSE_TIMEOUT,
            "wavemeter_port": wavemeter.WAVEMETER_PORT,
            "wavemeter_read_timeout_s": wavemeter.READ_TIMEOUT_S,
            "wavemeter_setpoint_timeout_s": wavemeter.SETPOINT_TIMEOUT_S,
            "wavemeter_default_channel": wavemeter.DEFAULT_CHANNEL,
        },
        "scope_enums": {
            enum.__name__: {m.name: m.value for m in enum}
            for enum in (
                scope.Channel,
                scope.Coupling,
                scope.TriggerCoupling,
                scope.TriggerSlope,
                scope.TriggerMode,
            )
        },
        "files": {"stem_pattern": io.STEM_PATTERN.pattern},
    }


def _default(function: Any, name: str) -> Any:
    import inspect

    return inspect.signature(function).parameters[name].default


# ---------------------------------------------------------------------------
# 4. recordings (the real on-disk schema)
# ---------------------------------------------------------------------------

#: What every field of a saved recording means. ``build_recordings`` fails when
#: this disagrees with what the save functions actually write, so the data
#: dictionary in the guide cannot describe a schema that no longer exists.
FIELD_DOCS: dict[str, dict[str, str]] = {
    "sweep": {
        "format": "Always `iyzee.numeric-recording`: identifies the file family.",
        "format_version": "Version of the file layout shared by sweeps and scope recordings.",
        "data_file": "Name of the `.npz` this manifest describes (they share one stem).",
        "data_sha256": "SHA-256 of the finished `.npz`; recompute it to detect copy or edit damage.",
        "run_metadata": "How the run ended and what it was configured with (a `RunRecord`); `null` when saved without one.",
        "run_metadata.run_id": "Eight hex digits naming the run; also in the log.",
        "run_metadata.status": "How the run ended: `running` (never finished), `completed`, `completed_with_errors`, `aborted`, `failed`.",
        "run_metadata.started_at_utc": "When the run began, UTC.",
        "run_metadata.finished_at_utc": "When the run ended, UTC; `null` while still `running`.",
        "run_metadata.config": "The analyzer settings the run programmed (an `AnalyzerConfig`).",
        "run_metadata.failed_steps": "One entry per step that raised.",
        "run_metadata.failed_steps.index": "Position of the failed step in the sequence.",
        "run_metadata.failed_steps.label": "The failed step's name.",
        "run_metadata.failed_steps.error_type": "Exception class name.",
        "run_metadata.failed_steps.error": "Exception message.",
        "points": "One entry per recorded step, in the same order as the rows of every `trace_*` array.",
        "points.label": "Step name, e.g. `rbw=24000Hz` or `freq=377.105207THz`.",
        "points.x_unit": "Unit of this step's `x_values` entry (`Hz` or `THz`).",
        "points.rbw_hz": "Bandwidth sweeps: resolution bandwidth used for this point.",
        "points.vbw_hz": "Bandwidth sweeps: video bandwidth used (twice the RBW).",
        "points.wavemeter_channel": "Frequency sweeps: wavemeter channel that was set and read.",
        "points.relax_time_s": "Frequency sweeps: settling time between setting the PID setpoint and reading.",
        "points.measured_frequency_thz": "Frequency sweeps: what the wavemeter read after settling, in THz.",
    },
    "scope": {
        "format": "Always `iyzee.numeric-recording`.",
        "format_version": "Version of the file layout shared by sweeps and scope recordings.",
        "data_file": "Name of the `.npz` this manifest describes.",
        "data_sha256": "SHA-256 of the finished `.npz`.",
        "kind": "`scope-acquisition`: how readers tell a scope recording from a sweep.",
        "schema_version": "Version of this manifest's own layout.",
        "measurement_id": "Random identifier of this acquisition.",
        "started_at_utc": "Acquisition start, UTC.",
        "completed_at_utc": "Acquisition end, UTC.",
        "software": "Which code wrote the file.",
        "software.name": "Always `iyzee`.",
        "software.version": "Installed iyzee version (`unknown` when run from a bare checkout).",
        "software.python": "Python version that wrote the file.",
        "instrument": "Where the data came from.",
        "instrument.driver": "Dotted path of the driver class.",
        "instrument.protocol": "Transport the driver used (`VISA (VXI-11)`).",
        "instrument.address": "Address the scope was reached at.",
        "instrument.identity": "The scope's `*IDN?` reply: make, model, serial, firmware. `null` if it did not answer.",
        "instrument.socket_timeout_s": "The driver's I/O timeout during the acquisition.",
        "configuration": "Requested versus applied scope configuration.",
        "configuration.requested_channel_settings": "What the form asked for, per channel.",
        "configuration.requested_trigger_settings": "What the form asked for, trigger and timebase.",
        "configuration.applied_channel_settings": "What the scope reported after the last Apply (the read-back), per channel.",
        "configuration.applied_trigger_settings": "What the scope reported for the trigger after the last Apply.",
        "acquisition": "How the capture was taken.",
        "acquisition.frozen": "`true` if a running acquisition was stopped so every channel comes from one capture.",
        "acquisition.prior_trigger_mode": "Trigger mode before the freeze (and restored afterwards).",
        "acquisition.warnings": "Non-fatal problems: could not freeze, could not restore, identity unavailable.",
        "waveforms": "One entry per recorded channel.",
        "waveforms.channel": "Channel name, e.g. `C1`; matches the suffix of its arrays.",
        "waveforms.value_unit": "Engineering unit of `value_<channel>` as the scope reports it.",
        "waveforms.time_unit": "Unit of the time axis as the scope reports it.",
        "waveforms.time_offset": "Time of the first sample relative to the trigger.",
        "waveforms.time_interval": "Spacing between samples.",
        "waveforms.vertical_gain": "Scope `VERTICAL_GAIN`: volts per ADC code.",
        "waveforms.vertical_offset": "Scope `VERTICAL_OFFSET`: subtracted after scaling.",
        "waveforms.stats": "Summary statistics of the calibrated values (non-finite samples excluded).",
        "waveforms.stats.sample_count": "Samples in the array.",
        "waveforms.stats.finite_count": "Samples that are finite and contribute to the statistics.",
        "waveforms.stats.min": "Smallest finite value.",
        "waveforms.stats.max": "Largest finite value.",
        "waveforms.stats.mean": "Mean of the finite values.",
        "waveforms.stats.rms": "Root-mean-square of the finite values.",
        "waveforms.stats.stddev": "Standard deviation of the finite values.",
        "waveforms.stats.peak_to_peak": "`max - min`.",
        "waveforms.stats.max_abs": "Largest absolute value.",
        "waveforms.stats.min_index": "Sample index of the minimum.",
        "waveforms.stats.max_index": "Sample index of the maximum.",
        "waveforms.stats.min_time": "Time of the minimum.",
        "waveforms.stats.max_time": "Time of the maximum.",
        "waveforms.raw_array": "Name of the raw-code array, or `null` if raw codes were not kept.",
        "data_semantics": "Plain-language statement of what each array family means.",
        "data_semantics.value_arrays": "How `value_*` were derived.",
        "data_semantics.raw_arrays": "What `raw_*` hold.",
        "data_semantics.time_arrays": "How `time_*` were derived.",
    },
}

ARRAY_DOCS: dict[str, dict[str, str]] = {
    "sweep": {
        "x_values": "One entry per point: the scanned quantity (Hz for RBW sweeps, THz for frequency sweeps).",
        "trace_squeezing": "Shape `(points, samples)`: the analyzer trace taken with the shutter open (frequency sweeps) or the squeezing trace (RBW sweeps), in dBm.",
        "trace_shot_noise": "Shape `(points, samples)`: the shot-noise reference trace, in dBm. A row of `NaN` means that point has no data for the trace.",
    },
    "scope": {
        "time_<ch>": "Time axis of channel `<ch>`: `time_offset + index * time_interval`.",
        "value_<ch>": "Calibrated samples in engineering units: `vertical_gain * raw - vertical_offset`.",
        "raw_<ch>": "The signed 16-bit codes exactly as the scope sent them (present when raw codes were kept).",
    },
}


#: Fields whose children are a documented dataclass rather than separate fields.
OPAQUE = {
    "run_metadata.config",
    "configuration.requested_channel_settings",
    "configuration.requested_trigger_settings",
    "configuration.applied_channel_settings",
    "configuration.applied_trigger_settings",
}


def _paths(value: Any, prefix: str = "") -> set[str]:
    """Dotted field paths of a JSON document; list items share their parent's path."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            found.add(path)
            if path not in OPAQUE:
                found |= _paths(item, path)
    elif isinstance(value, list):
        for item in value:
            found |= _paths(item, prefix)
    return found


def build_recordings() -> dict[str, Any]:
    import numpy as np

    from iyzee.devices.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
    from iyzee.experiment import AnalyzerConfig, RunRecord, StepResult, save_step_results
    from iyzee.experiment.io import load_recording
    from iyzee.scope_workflows import (
        ChannelSettings,
        ScopeAcquisition,
        ScopeWaveform,
        TriggerSettings,
        _stats,
        save_scope_acquisition,
    )

    rng = np.random.default_rng(0)
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        savedir = Path(tmp)

        # -- a sweep: one RBW point and one frequency point, so both families' fields exist
        record = RunRecord(config=asdict(AnalyzerConfig()))
        record.on_step(1, 2, type("S", (), {"label": "freq=0"})(), None, TimeoutError("example"))
        record.finish()
        samples = 4
        results = [
            StepResult(
                "rbw=24000Hz",
                24e3,
                "Hz",
                {
                    "squeezing": list(rng.normal(-60, 1, samples)),
                    "shot_noise": list(rng.normal(-58, 1, samples)),
                },
                {"rbw_hz": 24e3, "vbw_hz": 48e3},
            ),
            StepResult(
                "freq=377.105207THz",
                377.105207,
                "THz",
                {
                    "squeezing": list(rng.normal(-60, 1, samples)),
                    "shot_noise": list(rng.normal(-58, 1, samples)),
                },
                {
                    "wavemeter_channel": 4,
                    "relax_time_s": 0.5,
                    "measured_frequency_thz": 377.1052071,
                },
            ),
        ]
        sweep_path = save_step_results(results, savedir, record.as_metadata(), name="sweep")
        sweep = load_recording(sweep_path)

        # -- a scope acquisition
        time = np.arange(5, dtype=np.float64) * 1e-6
        values = rng.normal(0, 0.1, 5)
        waveforms = tuple(
            ScopeWaveform(
                channel=channel,
                time=time,
                values=values,
                raw_codes=np.arange(5, dtype=np.int16),
                value_unit="V",
                time_unit="S",
                time_offset=0.0,
                time_interval=1e-6,
                vertical_gain=1e-3,
                vertical_offset=0.0,
                stats=_stats(time, values),
            )
            for channel in (Channel.C1, Channel.C2)
        )
        channel_settings = tuple(
            ChannelSettings(c, True, 0.1, 0.0, Coupling.DC_1M) for c in (Channel.C1, Channel.C2)
        )
        trigger = TriggerSettings(
            Channel.C1, TriggerMode.NORMAL, TriggerSlope.POSITIVE, TriggerCoupling.DC, 0.0, 1e-6
        )
        acquisition = ScopeAcquisition(
            measurement_id="0" * 32,
            started_at_utc="2026-01-01T00:00:00.000000Z",
            completed_at_utc="2026-01-01T00:00:01.000000Z",
            instrument_address="10.0.0.5",
            socket_timeout_s=10.0,
            requested_channel_settings=channel_settings,
            requested_trigger_settings=trigger,
            applied_channel_settings=channel_settings,
            applied_trigger_settings=trigger,
            waveforms=waveforms,
            instrument_id="LECROY,WS,0,0",
            frozen=True,
            prior_trigger_mode=TriggerMode.AUTO,
            warnings=("example warning",),
        )
        scope_path = save_scope_acquisition(acquisition, savedir)
        scope_recording = load_recording(scope_path)

        for family, recording in (("sweep", sweep), ("scope", scope_recording)):
            manifest = recording.metadata
            array_names = {
                re.sub(r"_(C\d)$", "_<ch>", name): {
                    "dtype": str(array.dtype),
                    "ndim": array.ndim,
                }
                for name, array in recording.arrays.items()
            }
            documented = FIELD_DOCS[family]
            actual = _paths(manifest)
            missing = sorted(actual - set(documented))
            stale = sorted(set(documented) - actual)
            if missing or stale:
                raise SystemExit(
                    f"FIELD_DOCS['{family}'] in scripts/docs_index.py is out of step with what "
                    f"the save functions write.\n  undocumented fields: {missing}\n"
                    f"  documented but not written: {stale}"
                )
            arrays_documented = set(ARRAY_DOCS[family])
            if arrays_documented != set(array_names):
                raise SystemExit(
                    f"ARRAY_DOCS['{family}'] is out of step with the saved arrays: "
                    f"{sorted(arrays_documented ^ set(array_names))}"
                )
            out[family] = {
                "stem_example": scope_path.stem if family == "scope" else sweep_path.stem,
                "manifest": manifest,
                "arrays": array_names,
                "field_docs": documented,
                "array_docs": ARRAY_DOCS[family],
            }
    return out


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


def build() -> tuple[dict[str, Any], list[str]]:
    symbols: dict[str, Any] = {}
    modules: dict[str, Any] = {}
    imports: dict[str, list[str]] = {}
    for path in python_files():
        file_symbols, record = index_file(path)
        symbols.update(file_symbols)
        module = _module_name(path)
        modules[module] = record
        if not module.startswith("tests"):
            imports[module] = sorted(i for i in internal_imports(path) if i != module)

    # Keep only edges that point at a real module of the package.
    imports = {m: sorted({i for i in deps if i in modules}) for m, deps in imports.items()}

    lookup = build_lookup(symbols)
    refs, labels, problems = scan_typst(lookup)

    guide_pointers: dict[str, list[str]] = {}
    for module, record in modules.items():
        for label in record["guide"]:
            guide_pointers.setdefault(label, []).append(module)
            if label not in labels:
                problems.append(f"{record['path']}: :guide:`{label}` has no <{label}> in the guide")

    index = {
        "symbols": symbols,
        "lookup": lookup,
        "modules": modules,
        "imports": imports,
        "xref": {"refs": refs, "labels": labels, "guide_pointers": guide_pointers},
    }
    return index, problems


def build_full() -> tuple[dict[str, Any], list[str]]:
    index, problems = build()
    index["facts"] = build_facts()
    index["recordings"] = build_recordings()
    return index, problems


def render(index: dict[str, Any]) -> str:
    return json.dumps(index, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    index, problems = build_full()
    if problems:
        print(f"{len(problems)} documentation reference problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    text = render(index)
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print("docs/code-index.json is stale; run scripts/docs_index.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: {len(index['symbols'])} symbols, "
        f"{len(index['xref']['refs'])} cited by the guide"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
