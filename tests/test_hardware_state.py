from tests.hardware.hw_state import MXA_SETTINGS, restore


def test_mxa_restore_disables_sweep_time_auto_before_restoring_span():
    values = {setting.name: "1" for setting in MXA_SETTINGS}
    commands = []

    failures = restore(commands.append, MXA_SETTINGS, values)

    assert failures == []
    assert commands.index("SWE:TIME:AUTO OFF") < commands.index("FREQ:SPAN 1")
    assert commands.index("SWE:TIME 1") < commands.index("SWE:TIME:AUTO 1")
