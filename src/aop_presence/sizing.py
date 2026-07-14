"""Bounded extent estimation for a cluster.

A 4Rx/3Tx array cannot resolve cross-range detail finer than its beamwidth, so
a raw bounding box overstates precision. Every cross-range figure here is
floored at the resolution cell for that range and flagged when the floor binds.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .types import DetectedPoint, TargetSize

if TYPE_CHECKING:
    from .config import DetectionConfig


def cross_range_cell_m(range_m: float, resolution_deg: float) -> float:
    """Width of one angular resolution cell at the given range."""
    if range_m <= 0.0:
        return 0.0
    half_angle_rad: float = math.radians(resolution_deg) / 2.0
    return 2.0 * range_m * math.tan(half_angle_rad)


def _extent(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return max(values) - min(values)


def estimate_size(points: tuple[DetectedPoint, ...], config: DetectionConfig) -> TargetSize:
    """Estimate width/height/depth, never claiming better than the resolution cell."""
    if not points:
        return TargetSize(0.0, 0.0, 0.0, 0.0, resolution_limited=True)
    ranges: list[float] = [point.range_m for point in points]
    mean_range: float = math.fsum(ranges) / len(ranges)
    az_cell: float = cross_range_cell_m(mean_range, config.azimuth_resolution_deg)
    el_cell: float = cross_range_cell_m(mean_range, config.elevation_resolution_deg)

    raw_width: float = _extent([point.x_m for point in points])
    raw_height: float = _extent([point.z_m for point in points])
    raw_depth: float = _extent(ranges)

    width: float = max(raw_width, az_cell)
    height: float = max(raw_height, el_cell)
    depth: float = max(raw_depth, config.range_resolution_m)
    limited: bool = raw_width <= az_cell or raw_height <= el_cell
    return TargetSize(
        width_m=width,
        height_m=height,
        depth_m=depth,
        cross_range_cell_m=az_cell,
        resolution_limited=limited,
    )
