from __future__ import annotations

from pathlib import Path

import pytest

from iyzee import config
from iyzee.config import IP, ConfigError, address, data_root


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("IYZEE_SCOPE_IP", "IYZEE_DATA_DIR", "IYZEE_NOISE_ANALYZER_IP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IYZEE_CONFIG", str(tmp_path / "config.toml"))  # absent: defaults


def test_defaults_are_the_lab_addresses():
    assert address(IP.SCOPE) == "10.140.1.220"
    assert address(IP.WAVEMETER) == "10.140.1.215"


def test_config_file_overrides_an_address_and_the_data_dir(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[addresses]\nscope = "192.0.2.7"\n\n[paths]\ndata = "/srv/iyzee"\n'
    )
    assert address(IP.SCOPE) == "192.0.2.7"
    assert address(IP.NOISE_ANALYZER) == "10.140.1.40"  # untouched keys keep their default
    assert data_root() == Path("/srv/iyzee")


def test_environment_beats_the_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "config.toml").write_text('[addresses]\nscope = "192.0.2.7"\n')
    monkeypatch.setenv("IYZEE_SCOPE_IP", "198.51.100.9")
    monkeypatch.setenv("IYZEE_DATA_DIR", str(tmp_path / "d"))
    assert address(IP.SCOPE) == "198.51.100.9"
    assert data_root() == tmp_path / "d"


def test_a_broken_config_file_is_an_error_not_a_silent_default(tmp_path: Path):
    (tmp_path / "config.toml").write_text("[addresses\nscope = ")
    with pytest.raises(ConfigError, match="config.toml"):
        address(IP.SCOPE)


def test_data_root_defaults_to_the_checkout_data_dir():
    assert data_root() == Path(config.__file__).resolve().parents[2] / "data"


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
