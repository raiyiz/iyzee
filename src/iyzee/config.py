"""Where things are: instrument addresses and the data directory.

The defaults describe the lab; each can be overridden without touching code,
in this order of precedence:

1. an environment variable (``IYZEE_<NAME>_IP``, ``IYZEE_DATA_DIR``),
2. the TOML file ``IYZEE_CONFIG`` points at, else ``<user config dir>/iyzee/config.toml``::

       [addresses]
       scope = "10.140.1.221"        # keys: power_supply, noise_analyzer, scope, wavemeter

       [paths]
       data = "/srv/iyzee-data"

3. the default below.

A config file that exists but cannot be parsed raises :class:`ConfigError`
rather than being ignored: silently talking to the wrong instrument is worse
than not starting.
"""

from __future__ import annotations

import os
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir


class IP(StrEnum):
    """Default IP addresses of the laboratory instruments (see :func:`address`)."""

    POWER_SUPPLY = "10.140.1.15"
    NOISE_ANALYZER = "10.140.1.40"
    SCOPE = "10.140.1.220"
    WAVEMETER = "10.140.1.119"


class ConfigError(RuntimeError):
    """The config file exists but cannot be used."""


def config_file() -> Path:
    override = os.environ.get("IYZEE_CONFIG")
    return Path(override) if override else Path(user_config_dir("iyzee")) / "config.toml"


def _load() -> dict[str, Any]:
    path = config_file()
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc


def address(instrument: IP) -> str:
    """The address to use for ``instrument`` (an :class:`IP` member)."""
    name = instrument.name.lower()
    from_env = os.environ.get(f"IYZEE_{instrument.name}_IP")
    if from_env:
        return from_env
    value = _load().get("addresses", {}).get(name)
    return str(value) if value else str(instrument.value)


def data_root() -> Path:
    """Directory that measurement data and logs are written under.

    Default: ``<repo>/data`` when running from a source checkout, else a
    per-user data directory (an installed package has no repo to write into).
    """
    from_env = os.environ.get("IYZEE_DATA_DIR")
    if from_env:
        return Path(from_env)
    configured = _load().get("paths", {}).get("data")
    if configured:
        return Path(configured)
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file():
        return checkout / "data"
    return Path(user_data_dir("iyzee")) / "data"
