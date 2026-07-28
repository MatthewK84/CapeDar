"""Argument parsing and wiring. This is the only surface a user touches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aop_presence.cli import (
    build_parser,
    build_sink,
    resolve_configure_mode,
    resolve_detection_config,
    validate,
)
from aop_presence.config import ConfigValidationError, DetectionConfig, preset_config
from aop_presence.gpio import NullSink
from aop_presence.resources import profile_name_for_preset

if TYPE_CHECKING:
    import argparse


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


# --- defaults must not drift ------------------------------------------------


def test_runs_with_no_arguments_at_all() -> None:
    args = parse()
    assert args.preset == "indoor"
    assert args.gpio == "auto"
    assert args.gpio_pin == "BOARD11"
    assert args.configure == "auto"
    assert args.radar_cfg is None
    assert args.detection_cfg is None
    assert not args.gui
    assert not args.json


def test_no_argument_config_is_the_plain_default() -> None:
    assert resolve_detection_config(parse()) == DetectionConfig()


def test_headless_is_the_default_interface() -> None:
    assert not parse().gui


# --- presets ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["indoor", "outdoor", "airborne"])
def test_every_preset_resolves_and_maps_to_a_profile(name: str) -> None:
    assert resolve_detection_config(parse("--preset", name)) == preset_config(name)
    assert profile_name_for_preset(name).endswith(".cfg")


def test_airborne_preset_caps_range_at_five_metres() -> None:
    assert resolve_detection_config(parse("--preset", "airborne")).max_range_m == 5.0


def test_unknown_preset_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit):
        parse("--preset", "submarine")


# --- override precedence ----------------------------------------------------


def test_detection_cfg_file_replaces_the_preset(tmp_path: Path) -> None:
    """A saved gate file must mean exactly what it says, not merge into a preset."""
    path = tmp_path / "gates.json"
    path.write_text(json.dumps({"max_range_m": 3.0}), encoding="utf-8")
    config = resolve_detection_config(parse("--preset", "airborne", "--detection-cfg", str(path)))
    assert config.max_range_m == 3.0
    assert config.min_target_separation_m == DetectionConfig().min_target_separation_m


def test_min_separation_flag_overrides_the_preset() -> None:
    config = resolve_detection_config(parse("--preset", "airborne", "--min-separation", "2.0"))
    assert config.min_target_separation_m == 2.0
    assert config.max_range_m == 5.0


def test_agl_enables_ground_rejection() -> None:
    config = resolve_detection_config(
        parse("--preset", "airborne", "--agl", "12", "--pitch", "25")
    )
    assert config.ground_rejection_enabled
    assert config.agl_altitude_m == 12.0
    assert config.sensor_pitch_deg == 25.0


def test_zero_agl_does_not_enable_ground_rejection() -> None:
    """Rejecting against a zero-height ground plane would delete everything."""
    assert not resolve_detection_config(parse("--agl", "0")).ground_rejection_enabled


def test_ego_speed_overrides_the_cloud_fit() -> None:
    config = resolve_detection_config(parse("--preset", "airborne", "--ego-speed", "6.5"))
    assert config.ego_motion_enabled
    assert not config.ego_estimate_from_cloud
    assert config.ego_forward_mps == 6.5


def test_movers_only_turns_on_ego_compensation() -> None:
    """A relative-velocity gate is meaningless without something to subtract."""
    config = resolve_detection_config(parse("--movers-only", "1.0"))
    assert config.ego_motion_enabled
    assert config.min_relative_velocity_mps == 1.0


def test_implausible_ego_speed_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="ego_forward_mps"):
        resolve_detection_config(parse("--ego-speed", "500"))


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--status-interval", "--stale-timeout"])
def test_nonpositive_intervals_are_rejected(flag: str) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        validate(parser, parser.parse_args([flag, "0"]))


def test_json_with_gui_is_rejected() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        validate(parser, parser.parse_args(["--gui", "--json"]))


def test_valid_arguments_pass_validation() -> None:
    parser = build_parser()
    validate(parser, parser.parse_args(["--preset", "airborne"]))


# --- configure mode ---------------------------------------------------------


def test_configure_defaults_to_auto() -> None:
    assert resolve_configure_mode(parse()) == "auto"


def test_deprecated_no_configure_still_means_never() -> None:
    assert resolve_configure_mode(parse("--no-configure")) == "never"


@pytest.mark.parametrize("mode", ["auto", "always", "never"])
def test_configure_modes_round_trip(mode: str) -> None:
    assert resolve_configure_mode(parse("--configure", mode)) == mode


# --- signal sink ------------------------------------------------------------


def test_gpio_off_yields_a_null_sink() -> None:
    assert isinstance(build_sink(parse("--gpio", "off")), NullSink)


def test_gpio_auto_degrades_off_hardware() -> None:
    """One command line has to work on both a Pi and a laptop."""
    assert isinstance(build_sink(parse("--gpio", "auto")), NullSink)


def test_gui_never_drives_the_signal_line() -> None:
    assert isinstance(build_sink(parse("--gui", "--gpio", "on")), NullSink)


def test_active_low_is_carried_through() -> None:
    assert parse("--gpio-active-low").gpio_active_low


# --- shipped gate files -----------------------------------------------------


@pytest.mark.parametrize(
    "name", ["detection_gates.json", "detection_gates_pi.json", "detection_gates.example.json"]
)
def test_shipped_gate_files_keep_the_antiphantom_stages(name: str) -> None:
    """A shipped default must retain temporal confirmation.

    detection_gates_debug.json is deliberately excluded: it exists to be
    maximally sensitive, and its name says so.
    """
    path = Path(__file__).resolve().parents[1] / "configs" / name
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("frames_to_confirm", 3) >= 2, "hysteresis disabled"
    if name != "detection_gates_pi.json":
        assert raw.get("cluster_min_points", 3) >= 2, "clustering disabled"


def test_every_shipped_gate_file_loads(tmp_path: Path) -> None:
    from aop_presence.config import load_detection_config

    configs = Path(__file__).resolve().parents[1] / "configs"
    files = sorted(configs.glob("detection_gates*.json"))
    assert len(files) >= 4
    for path in files:
        assert load_detection_config(path).max_range_m > 0.0
