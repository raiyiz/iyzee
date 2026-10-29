from __future__ import annotations

import pytest

from iyzee.tui.commands import Command, CommandError, CommandRegistry, parse_si


def make_registry(calls: list) -> CommandRegistry:
    def record(name):
        return lambda args: calls.append((name, args)) or f"{name} ok"

    return CommandRegistry(
        [
            Command(
                "connect",
                record("connect"),
                "connect",
                aliases=("c",),
                complete=lambda p: [k for k in ("scope", "mxa", "wavemeter") if k.startswith(p)],
            ),
            Command("console", record("console"), "console page"),
            Command("sweep", record("sweep"), "sweep page", aliases=("s",)),
            Command("quit", record("quit"), "quit", aliases=("q",)),
        ]
    )


def test_parse_si_understands_suffixes_and_rejects_garbage():
    for text, value in (
        ("2u", 2e-6),
        ("500ns", 500e-9),
        ("1.5m", 1.5e-3),
        ("1e-6", 1e-6),
        ("3", 3.0),
        ("2M", 2e6),
        ("5ms", 5e-3),
    ):
        assert parse_si(text) == pytest.approx(value)
    with pytest.raises(CommandError, match="not a number"):
        parse_si("fast")


def test_names_aliases_and_prefixes_resolve_and_misses_are_user_errors():
    # -- exact name alias and unique prefix resolve and pass arguments
    calls: list = []
    registry = make_registry(calls)

    assert registry.execute(":connect scope mxa") == "connect ok"
    registry.execute("c scope")  # alias
    registry.execute("swe")  # unique prefix
    registry.execute(":quit")
    assert calls == [
        ("connect", ["scope", "mxa"]),
        ("connect", ["scope"]),
        ("sweep", []),
        ("quit", []),
    ]

    # -- ambiguous and unknown commands are user errors
    registry = make_registry([])
    with pytest.raises(CommandError, match=r"ambiguous :con.*connect, console"):
        registry.execute("con")
    with pytest.raises(CommandError, match="unknown command :zap"):
        registry.execute("zap")


def test_completion_and_help_list_commands_and_their_arguments():
    # -- tab completion of command words and arguments
    registry = make_registry([])
    assert registry.complete("swe") == ("sweep ", ["sweep"])
    line, candidates = registry.complete("con")
    assert line == "con" and candidates == ["connect", "console"]  # common prefix is "con" itself
    assert registry.complete("connect sc") == ("connect scope ", ["scope"])
    line, candidates = registry.complete("connect ")
    assert candidates == ["scope", "mxa", "wavemeter"] and line == "connect "
    assert registry.complete("connect scope ")[1] == [
        "mxa",
        "wavemeter",
    ]  # already-given ones are skipped
    assert registry.complete("zzz") == ("zzz", [])

    # -- help lists every command and details one
    registry = make_registry([])
    listing = registry.help_text()
    assert all(f":{name}" in listing for name in registry.names)
    detail = registry.help_text("c")
    assert ":connect, :c" in detail and "connect" in detail
