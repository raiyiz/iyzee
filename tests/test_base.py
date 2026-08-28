from base import BaseDevice, TestDevice


def test_base_device_without_ip_uses_test_device():
    device = BaseDevice()
    assert isinstance(device.instrument, TestDevice)


def test_test_device_records_writes(capsys):
    TestDevice().write("FREQ:CENT 1000000")
    output = capsys.readouterr().out
    assert "FREQ:CENT 1000000" in output


def test_test_device_trace_query(monkeypatch):
    monkeypatch.setattr(TestDevice, "get_trace_data", lambda self: [1.0, 2.0, 3.0])
    response = TestDevice().query(":TRACe:DATA? TRACE1")
    assert response == "1.0,2.0,3.0"
