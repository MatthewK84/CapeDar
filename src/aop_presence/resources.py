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


def default_radar_cfg_text() -> str:
    """Return the bundled TI .cfg profile as text."""
    try:
        return resources.files(DATA_PACKAGE).joinpath(DEFAULT_CFG_NAME).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError) as exc:
        raise ConfigError(f"Bundled radar profile is missing: {exc}") from exc


def parse_config_text(text: str) -> list[str]:
    """Split .cfg text into commands, dropping comments and blank lines."""
    lines: list[str] = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("%")]


def default_radar_commands() -> list[str]:
    """Return the bundled profile as a list of CLI commands."""
    commands: list[str] = parse_config_text(default_radar_cfg_text())
    if not commands:
        raise ConfigError("Bundled radar profile contains no commands")
    return commands
