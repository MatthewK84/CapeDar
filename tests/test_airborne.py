"""Airborne corrections, the 5 m working range, and ground-based regression cover."""

from __future__ import annotations

import math
import struct

import pytest

from aop_presence.airborne import (
    apply_airborne_gates,
    estimate_forward_speed,
    expected_static_doppler_mps,
    height_above_ground_m,
    relative_doppler_mps,
)
from aop_presence.config import (
    AIRBORNE_5M,
    ConfigValidationError,
    DetectionConfig,
    preset_config,
)
from aop_presence.custom_types import DetectedPoint, DetectionReport, OccupancyState
from aop_presence.parser import parse_temperature
from aop_presence.pipeline import DetectionPipeline
from aop_presence.protocol import TEMPERATURE_STRUCT
from aop_presence.resources import bundled_radar_commands, profile_name_for_preset
from aop_presence.simulator import make_frame

AIRBORNE = preset_config("airborne")


def static_field(forward_mps: float, bearings_deg: tuple[float, ...]) -> tuple[DetectedPoint, ...]:
    """A ring of static returns as seen from a platform flying forward."""
    points: list[DetectedPoint] = []
    for bearing in bearings_deg:
        rad = math.radians(bearing)
        x, y = 4.0 * math.sin(rad), 4.0 * math.cos(rad)
        doppler = -forward_mps * (y / 4.0)
        points.append(DetectedPoint(x, y, 0.0, doppler, 25.0))
    return tuple(points)


def body_at(
    range_m: float, bearing_deg: float, doppler: float = -0.3
) -> tuple[DetectedPoint, ...]:
    """A small cloud of returns standing in for one object."""
    rad = math.radians(bearing_deg)
    cx, cy = range_m * math.sin(rad), range_m * math.cos(rad)
    return tuple(
        DetectedPoint(cx + dx, cy + dy, dz, doppler, 25.0)
        for dx, dy, dz in ((-0.05, 0.0, 0.0), (0.05, 0.0, 0.1), (0.0, 0.05, -0.1))
    )


# --- ego-motion -------------------------------------------------------------


def test_static_point_ahead_closes_at_platform_speed() -> None:
    ahead = DetectedPoint(0.0, 5.0, 0.0, 0.0, 25.0)
    assert expected_static_doppler_mps(ahead, 6.0) == pytest.approx(-6.0)


def test_static_point_abeam_shows_no_doppler() -> None:
    """Nothing at 90 degrees changes range as the platform flies past it."""
    abeam = DetectedPoint(5.0, 0.0, 0.0, 0.0, 25.0)
    assert expected_static_doppler_mps(abeam, 6.0) == pytest.approx(0.0, abs=1e-9)


def test_forward_speed_recovered_from_static_field() -> None:
    points = static_field(7.5, (-40.0, -25.0, -10.0, 0.0, 10.0, 25.0, 40.0))
    estimate = estimate_forward_speed(points)
    assert estimate.trusted
    assert estimate.forward_mps == pytest.approx(7.5, abs=0.05)
    assert estimate.residual_mps < 0.05


def test_compensation_zeroes_static_returns() -> None:
    points = static_field(6.0, (-30.0, 0.0, 30.0))
    for point in points:
        assert relative_doppler_mps(point, 6.0) == pytest.approx(0.0, abs=1e-6)


def test_mover_survives_compensation() -> None:
    """The whole point: a real mover keeps its signature after correction."""
    ahead = DetectedPoint(0.0, 4.0, 0.0, -8.0, 25.0)
    assert relative_doppler_mps(ahead, 6.0) == pytest.approx(-2.0)


def test_fit_refused_without_bearing_spread() -> None:
    """Points bunched at one bearing cannot constrain the fit."""
    points = static_field(6.0, (0.0, 0.5, 1.0, 1.5, 2.0, 2.5))
    assert not estimate_forward_speed(points).trusted


def test_fit_refused_with_too_few_points() -> None:
    assert not estimate_forward_speed(static_field(6.0, (-30.0, 30.0))).trusted


def test_fit_refused_at_implausible_speed() -> None:
    points = static_field(400.0, (-40.0, -20.0, 0.0, 20.0, 40.0, 50.0))
    assert not estimate_forward_speed(points).trusted


# --- ground rejection -------------------------------------------------------


def test_level_sensor_puts_boresight_at_own_height() -> None:
    ahead = DetectedPoint(0.0, 5.0, 0.0, 0.0, 25.0)
    assert height_above_ground_m(ahead, 10.0, 0.0) == pytest.approx(10.0)


def test_pitched_sensor_finds_the_ground() -> None:
    """At 30 degrees nose-down, a return 10 m out is 5 m below the aircraft."""
    ahead = DetectedPoint(0.0, 10.0, 0.0, 0.0, 25.0)
    assert height_above_ground_m(ahead, 5.0, 30.0) == pytest.approx(0.0, abs=1e-6)


def test_ground_returns_are_rejected_and_targets_kept() -> None:
    config = AIRBORNE.with_overrides(
        agl_altitude_m=3.0, sensor_pitch_deg=30.0, ground_rejection_enabled=True
    )
    ground = DetectedPoint(0.0, 6.0, 0.0, -1.0, 25.0)
    standing = DetectedPoint(0.0, 4.0, 1.2, -1.0, 25.0)
    survivors, _ = apply_airborne_gates((ground, standing), config)
    assert survivors == (standing,)


def test_ground_rejection_demands_an_altitude() -> None:
    with pytest.raises(ConfigValidationError, match="agl_altitude_m"):
        DetectionConfig(ground_rejection_enabled=True)


def test_movers_only_drops_static_returns() -> None:
    config = AIRBORNE.with_overrides(
        ego_estimate_from_cloud=False, ego_forward_mps=6.0, min_relative_velocity_mps=1.0
    )
    static = DetectedPoint(0.0, 4.0, 0.0, -6.0, 25.0)
    mover = DetectedPoint(0.0, 4.0, 0.0, -9.0, 25.0)
    survivors, _ = apply_airborne_gates((static, mover), config)
    assert survivors == (mover,)


# --- the 5 m requirement ----------------------------------------------------


def confirm(
    pipeline: DetectionPipeline, points: tuple[DetectedPoint, ...], frames: int
) -> DetectionReport:
    report: DetectionReport | None = None
    for number in range(frames):
        report = pipeline.process(make_frame(number, points))
    assert report is not None
    return report


def test_single_object_detected_at_five_metres() -> None:
    report = confirm(DetectionPipeline(AIRBORNE), body_at(4.8, 0.0), 12)
    assert report.state.value == "PRESENT"
    assert report.distinct_count == 1
    assert not report.multi_target


def test_two_objects_resolved_at_five_metres() -> None:
    """1.6 m apart at 4.8 m clears the 1.32 m azimuth cell, so both must survive."""
    pair = body_at(4.8, -9.5) + body_at(4.8, 9.5)
    separation = math.dist((pair[0].x_m, pair[0].y_m), (pair[-1].x_m, pair[-1].y_m))
    assert separation > AIRBORNE.min_target_separation_m
    report = confirm(DetectionPipeline(AIRBORNE), pair, 20)
    assert report.distinct_count == 2
    assert report.multi_target
    assert report.occupancy is OccupancyState.MULTIPLE


def test_unresolvable_pair_at_five_metres_reads_as_one() -> None:
    """Two objects inside one azimuth cell are physically one return, not two."""
    pair = body_at(4.8, -4.0) + body_at(4.8, 4.0)
    report = confirm(DetectionPipeline(AIRBORNE), pair, 20)
    assert report.distinct_count == 1
    assert not report.multi_target


def test_beyond_five_metres_is_rejected() -> None:
    report = confirm(DetectionPipeline(AIRBORNE), body_at(6.5, 0.0), 12)
    assert report.state.value == "ABSENT"
    assert report.distinct_count == 0


def test_airborne_separation_matches_the_azimuth_cell_at_five_metres() -> None:
    cell_m = 2.0 * 5.0 * math.tan(math.radians(AIRBORNE.azimuth_resolution_deg) / 2.0)
    assert AIRBORNE.min_target_separation_m == pytest.approx(cell_m, abs=0.05)


# --- backward compatibility -------------------------------------------------


def test_defaults_leave_every_airborne_gate_inert() -> None:
    config = DetectionConfig()
    assert not config.airborne_corrections_active
    assert not config.ego_motion_enabled
    assert not config.ground_rejection_enabled
    assert config.min_relative_velocity_mps == 0.0


def test_inert_gates_pass_every_point_through_untouched() -> None:
    points = static_field(6.0, (-30.0, 0.0, 30.0))
    survivors, ego = apply_airborne_gates(points, DetectionConfig())
    assert survivors == points
    assert not ego.trusted


def test_ground_based_report_carries_no_ego_field() -> None:
    report = DetectionPipeline(DetectionConfig()).process(make_frame(0, body_at(2.0, 0.0)))
    assert report.ego is None


def test_airborne_report_carries_the_ego_estimate() -> None:
    report = DetectionPipeline(AIRBORNE).process(make_frame(0, body_at(3.0, 0.0)))
    assert report.ego is not None


def test_indoor_preset_is_the_untouched_default() -> None:
    assert preset_config("indoor") == DetectionConfig()


def test_unknown_preset_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="Unknown preset"):
        preset_config("submarine")


# --- chirp profile ----------------------------------------------------------


def test_airborne_profile_is_bundled_and_parses() -> None:
    commands = bundled_radar_commands(profile_name_for_preset("airborne"))
    assert commands[0] == "sensorStop"
    assert commands[-1] == "sensorStart"


def test_airborne_profile_opens_the_doppler_gate() -> None:
    """The indoor profile clamps Doppler to +/-1 m/s, which grounds the aircraft."""
    commands = bundled_radar_commands("airborne_5m.cfg")
    doppler = [c for c in commands if c.startswith("cfarFovCfg") and c.split()[2] == "1"]
    assert len(doppler) == 1
    assert float(doppler[0].split()[3]) <= -13.0
    assert float(doppler[0].split()[4]) >= 13.0


def test_airborne_profile_runs_at_twenty_hertz() -> None:
    commands = bundled_radar_commands("airborne_5m.cfg")
    frame = next(c for c in commands if c.startswith("frameCfg"))
    assert float(frame.split()[5]) == pytest.approx(50.0)


def test_airborne_chirp_reaches_the_velocity_it_claims() -> None:
    """Recompute Vmax from the chirp so the file cannot drift from its header."""
    commands = bundled_radar_commands("airborne_5m.cfg")
    profile = next(c for c in commands if c.startswith("profileCfg")).split()
    idle_us, ramp_us, slope = float(profile[3]), float(profile[5]), float(profile[8])
    frame = next(c for c in commands if c.startswith("frameCfg")).split()
    n_tx = int(frame[2]) - int(frame[1]) + 1
    centre_hz = 60e9 + (slope * ramp_us * 1e6) / 2.0
    wavelength = 3.0e8 / centre_hz
    v_max = wavelength / (4.0 * (idle_us + ramp_us) * 1e-6 * n_tx)
    assert v_max > 10.0


def test_airborne_preset_matches_its_chirp_resolution() -> None:
    commands = bundled_radar_commands("airborne_5m.cfg")
    profile = next(c for c in commands if c.startswith("profileCfg")).split()
    slope, n_adc, rate_ksps = float(profile[8]), float(profile[10]), float(profile[11])
    bandwidth_hz = slope * 1e12 * (n_adc / (rate_ksps * 1e3))
    assert AIRBORNE_5M.range_resolution_m == pytest.approx(3.0e8 / (2 * bandwidth_hz), abs=0.005)


def test_indoor_profile_is_left_alone() -> None:
    commands = bundled_radar_commands("default.cfg")
    profile = next(c for c in commands if c.startswith("profileCfg"))
    assert profile == "profileCfg 0 60 359 7 57.14 0 0 70 1 256 5209 0 0 158"


# --- thermal ----------------------------------------------------------------


def encode_temperature(valid: int, sensors: tuple[int, ...]) -> bytes:
    return struct.pack(TEMPERATURE_STRUCT, valid, 1234, *sensors)


def test_temperature_tlv_decodes() -> None:
    report = parse_temperature(encode_temperature(1, (41, 42, 43, 44, 51, 52, 53, 60, 71, 72)))
    assert report is not None
    assert report.valid
    assert report.hottest_c == pytest.approx(72.0)


def test_temperature_tlv_reports_invalid_flag() -> None:
    report = parse_temperature(encode_temperature(0, (40,) * 10))
    assert report is not None
    assert not report.valid


def test_short_temperature_payload_is_ignored() -> None:
    assert parse_temperature(b"\x00\x01") is None


def test_frame_without_temperature_tlv_is_none() -> None:
    assert make_frame(0, ()).temperature is None
