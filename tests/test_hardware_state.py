from tests.hardware.hw_state import MXA_SETTINGS, restore


def test_mxa_restore_disables_sweep_time_auto_before_restoring_span():
    values = {setting.name: "1" for setting in MXA_SETTINGS}
    commands = []

    failures = restore(commands.append, MXA_SETTINGS, values)

    assert failures == []
    assert commands.index("SWE:TIME:AUTO OFF") < commands.index("FREQ:SPAN 1")
    assert commands.index("SWE:TIME 1") < commands.index("SWE:TIME:AUTO 1")



def test_mxa_restore_does_not_reenable_auto_sweep_time_at_zero_span():
    values = {setting.name: "1" for setting in MXA_SETTINGS}
    values["span"] = "0"
    commands = []

    failures = restore(commands.append, MXA_SETTINGS, values)

    assert failures == []
    assert "SWE:TIME:AUTO OFF" in commands
    assert "SWE:TIME:AUTO 1" not in commands
