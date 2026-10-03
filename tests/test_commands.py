from __future__ import annotations

import pytest

from iyzee.tui.commands import CommandError, parse_command, suggestions


def test_parse_command_accepts_colon_prefix_and_shell_quoting() -> None:
    assert parse_command(":scope sync") == ["scope", "sync"]
    assert parse_command('connect "mxa"') == ["connect", "mxa"]


def test_parse_command_rejects_unterminated_quotes() -> None:
    with pytest.raises(CommandError, match="cannot parse command"):
        parse_command(':connect "mxa')


def test_suggestions_follow_the_command_hierarchy() -> None:
    assert ":scope" in suggestions(":sc")
    assert suggestions(":scope ") == [":scope sync", ":scope acquire"]
    assert ":connect scope" in suggestions(":connect ")
