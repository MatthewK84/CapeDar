"""Per-point gating.

This is the first of three stages that stop the sensor reporting phantom
objects. A point survives only if it is strong enough, at a plausible range,
and inside the forward field of view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DetectionConfig
    from .custom_types import DetectedPoint


def passes_snr(point: DetectedPoint, config: DetectionConfig) -> bool:
    return point.snr_db >= config.min_snr_db


def passes_range(point: DetectedPoint, config: DetectionConfig) -> bool:
    range_m: float = point.range_m
    return config.min_range_m <= range_m <= config.max_range_m


def passes_angles(point: DetectedPoint, config: DetectionConfig) -> bool:
    """Reject anything not in front of the antenna face, or off to the sides.

    Elevation matters more at close range than intuition suggests. A person
    standing 1 m away puts their head near 39 degrees above boresight, which a
    40 degree elevation gate clips. The same person at 4 m sits inside 12
    degrees. Narrow elevation gates therefore blind the sensor to near targets
    while leaving far ones untouched.
    """
    if point.y_m <= 0.0:
        return False
    if abs(point.azimuth_deg) > config.max_azimuth_deg:
        return False
    return abs(point.elevation_deg) <= config.max_elevation_deg


def passes_height(point: DetectedPoint, config: DetectionConfig) -> bool:
    """Reject returns too far above or below the sensor plane."""
    return abs(point.z_m) <= config.max_abs_z_m


def passes_field_of_view(point: DetectedPoint, config: DetectionConfig) -> bool:
    """Every geometric gate, kept as one call for the hot path."""
    return passes_angles(point, config) and passes_height(point, config)


def is_valid_point(point: DetectedPoint, config: DetectionConfig) -> bool:
    """Apply every per-point gate, cheapest first."""
    if not passes_snr(point, config):
        return False
    if not passes_range(point, config):
        return False
    return passes_field_of_view(point, config)


def gate_points(
    points: tuple[DetectedPoint, ...], config: DetectionConfig
) -> tuple[DetectedPoint, ...]:
    """Return only the points that clear every gate."""
    return tuple(point for point in points if is_valid_point(point, config))
