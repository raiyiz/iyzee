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


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2u", 2e-6),
        ("500ns", 500e-9),
        ("1.5m", 1.5e-3),
        ("1e-6", 1e-6),
        ("3", 3.0),
        ("2M", 2e6),
        ("10Hz", 10.0),
        ("5ms", 5e-3),
    ],
)
def test_parse_si(text, value):
    assert parse_si(text) == pytest.approx(value)


def test_parse_si_rejects_garbage():
    with pytest.raises(CommandError, match="not a number"):
        parse_si("fast")


def test_exact_name_alias_and_unique_prefix_resolve_and_pass_arguments():
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


def test_an_exact_alias_beats_prefix_matching_and_blank_lines_do_nothing():
    calls: list = []
    registry = make_registry(calls)
    registry.execute("s")  # would be ambiguous as a prefix of sweep/... only if aliases didn't win
    assert calls == [("sweep", [])]
    assert registry.execute("  :  ") is None


def test_ambiguous_and_unknown_commands_are_user_errors():
    registry = make_registry([])
    with pytest.raises(CommandError, match="ambiguous :con.*connect, console"):
        registry.execute("con")
    with pytest.raises(CommandError, match="unknown command :zap"):
        registry.execute("zap")


def test_quoted_arguments_stay_together_and_bad_quoting_is_an_error():
    calls: list = []
    make_registry(calls).execute('connect "a b" c')
    assert calls == [("connect", ["a b", "c"])]
    with pytest.raises(CommandError, match="cannot parse"):
        make_registry([]).execute('connect "oops')


def test_a_later_registration_shadows_an_earlier_one():
    registry = make_registry([])
    registry.add(Command("sweep", lambda args: "page-specific", "shadow"))
    assert registry.execute("sweep") == "page-specific"


def test_tab_completion_of_command_words_and_arguments():
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


def test_help_lists_every_command_and_details_one():
    registry = make_registry([])
    listing = registry.help_text()
    assert all(f":{name}" in listing for name in registry.names)
    detail = registry.help_text("c")
    assert ":connect, :c" in detail and "connect" in detail
