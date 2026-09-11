from iyzee.tui.navigation.commands import Command, CommandRegistry
from iyzee.tui.navigation.mode import Mode


def test_navigation_modes_are_explicit() -> None:
    assert [Mode.NORMAL, Mode.INSERT, Mode.COMMAND] == list(Mode)


def test_command_registry_registers_and_lists_commands() -> None:
    called: list[list[str]] = []
    registry = CommandRegistry()
    registry.register(Command("sweep", "Open Sweep", called.append))

    command = registry.get("sweep")
    assert command is not None
    command.callback(["bandwidth"])
    assert called == [["bandwidth"]]
    assert registry.names() == ("sweep",)
