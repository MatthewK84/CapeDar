"""Configuration that ships inside the package.

The default radar profile is package data, not a file on disk beside the repo.
That is what allows ``capedar`` to run from any working directory, from a
systemd unit with no WorkingDirectory, or from a wheel installed into a venv,
without anyone passing --radar-cfg. Nothing about normal operation should
require the user to locate a file.
"""

from __future__ import annotations

from importlib import resources
from typing import Final

from .protocol import ConfigError

DATA_PACKAGE: Final[str] = "aop_presence.data"
DEFAULT_CFG_NAME: Final[str] = "default.cfg"
AIRBORNE_CFG_NAME: Final[str] = "airborne_5m.cfg"

# Detection preset name -> bundled chirp profile. The two must move together:
# a detection config declaring 5 m range resolution 0.125 is only truthful when
# the radar is actually running the chirp that produces it.
PROFILE_FOR_PRESET: Final[dict[str, str]] = {
    "indoor": DEFAULT_CFG_NAME,
    "airborne": AIRBORNE_CFG_NAME,
}


def bundled_cfg_text(name: str = DEFAULT_CFG_NAME) -> str:
    """Return a bundled TI .cfg profile as text."""
    try:
        return resources.files(DATA_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError) as exc:
        raise ConfigError(f"Bundled radar profile {name} is missing: {exc}") from exc


def default_radar_cfg_text() -> str:
    """Return the bundled indoor TI .cfg profile as text."""
    return bundled_cfg_text(DEFAULT_CFG_NAME)


def profile_name_for_preset(preset: str) -> str:
    """Map a detection preset onto the chirp profile it was tuned against."""
    name: str | None = PROFILE_FOR_PRESET.get(preset)
    if name is None:
        raise ConfigError(f"No bundled chirp profile for preset {preset!r}")
    return name


def parse_config_text(text: str) -> list[str]:
    """Split .cfg text into commands, dropping comments and blank lines."""
    lines: list[str] = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("%")]


def bundled_radar_commands(name: str = DEFAULT_CFG_NAME) -> list[str]:
    """Return a bundled profile as a list of CLI commands."""
    commands: list[str] = parse_config_text(bundled_cfg_text(name))
    if not commands:
        raise ConfigError(f"Bundled radar profile {name} contains no commands")
    return commands


def default_radar_commands() -> list[str]:
    """Return the bundled indoor profile as a list of CLI commands."""
    return bundled_radar_commands(DEFAULT_CFG_NAME)
