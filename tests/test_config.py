from __future__ import annotations

from pathlib import Path

import pytest

from iyzee.config import IP, address, data_root


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("IYZEE_SCOPE_IP", "IYZEE_DATA_DIR", "IYZEE_NOISE_ANALYZER_IP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IYZEE_CONFIG", str(tmp_path / "config.toml"))  # absent: defaults


def test_the_config_file_overrides_defaults_and_the_environment_beats_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "config.toml").write_text(
        '[addresses]\nscope = "192.0.2.7"\n\n[paths]\ndata = "/srv/iyzee"\n'
    )
    assert address(IP.SCOPE) == "192.0.2.7"
    assert data_root() == Path("/srv/iyzee")

    monkeypatch.setenv("IYZEE_SCOPE_IP", "198.51.100.9")
    monkeypatch.setenv("IYZEE_DATA_DIR", str(tmp_path / "d"))
    assert address(IP.SCOPE) == "198.51.100.9"
    assert data_root() == tmp_path / "d"


def test_drivers_pick_the_configured_address_at_construction(tmp_path: Path):
    from iyzee.devices.handles import ScopeHandle
    from iyzee.devices.mxa import KeysightMXA

    (tmp_path / "config.toml").write_text(
        '[addresses]\nscope = "192.0.2.7"\nnoise_analyzer = "192.0.2.8"\n'
    )
    assert ScopeHandle()._ip == "192.0.2.7"
    assert KeysightMXA(resource_manager=object()).ip == "192.0.2.8"
    assert (
        KeysightMXA("203.0.113.1", resource_manager=object()).ip == "203.0.113.1"
    )  # explicit wins
