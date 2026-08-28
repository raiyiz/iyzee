from base import BaseDevice, TestDevice


def test_base_device_without_ip_uses_test_device():
    device = BaseDevice()
    assert isinstance(device.instrument, TestDevice)


def test_test_device_records_writes(capsys):
    device = TestDevice()
    device.write("FREQ:CENT 1000000")
    output = capsys.readouterr().out
    assert "FREQ:CENT 1000000" in output


def test_test_device_returns_fixture_trace(tmp_path, monkeypatch):
    fixture = tmp_path / "test_data.np"
    fixture.write_bytes(b"\x93NUMPY\x01\x00\x76\x00{'descr': '<f8', 'fortran_order': False, 'shape': (2,), }                                                          \n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    monkeypatch.chdir(tmp_path)
    assert len(TestDevice().get_trace_data()) == 2
