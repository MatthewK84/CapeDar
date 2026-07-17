"""Immutable data shapes for the AWR6843AOP presence pipeline.

Every structure crossing a module boundary is declared here (Principle 8).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

DEFAULT_NOISE_DB: Final[float] = 0.0


class PresenceState(StrEnum):
    """Output of the presence hysteresis state machine."""

    ABSENT = "ABSENT"
    PRESENT = "PRESENT"


class OccupancyState(StrEnum):
    """How many distinct objects are confirmed in front of the sensor.

    MULTIPLE is the condition that drives the GPIO signal line. It is a
    separate state machine from PresenceState because cluster counts are far
    less stable than mere presence and need their own, slower, hysteresis.
    """

    EMPTY = "EMPTY"
    SINGLE = "SINGLE"
    MULTIPLE = "MULTIPLE"


@dataclass(frozen=True, slots=True)
class DetectedPoint:
    """One point from MMWDEMO_OUTPUT_MSG_DETECTED_POINTS, in sensor coordinates.

    Axes follow the TI convention: +x right, +y boresight (forward), +z up.
    """

    x_m: float
    y_m: float
    z_m: float
    doppler_mps: float
    snr_db: float = 0.0
    noise_db: float = DEFAULT_NOISE_DB

    @property
    def range_m(self) -> float:
        return math.sqrt(self.x_m**2 + self.y_m**2 + self.z_m**2)

    @property
    def azimuth_deg(self) -> float:
        return math.degrees(math.atan2(self.x_m, self.y_m))

    @property
    def elevation_deg(self) -> float:
        ground_range: float = math.hypot(self.x_m, self.y_m)
        return math.degrees(math.atan2(self.z_m, ground_range))


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """40-byte MmwDemo_output_message_header."""

    version: int
    total_packet_len: int
    platform: int
    frame_number: int
    time_cpu_cycles: int
    num_detected_obj: int
    num_tlvs: int
    subframe_number: int


@dataclass(frozen=True, slots=True)
class RadarFrame:
    """A fully parsed UART frame."""

    header: FrameHeader
    points: tuple[DetectedPoint, ...]
    host_timestamp_s: float


@dataclass(frozen=True, slots=True)
class TargetSize:
    """Bounded physical extent of a cluster.

    ``width_m`` and ``height_m`` are cross-range and therefore limited by the
    angular resolution of the array; they are lower bounds floored at the
    resolution cell at that range.
    """

    width_m: float
    height_m: float
    depth_m: float
    cross_range_cell_m: float
    resolution_limited: bool

    @property
    def frontal_area_m2(self) -> float:
        return self.width_m * self.height_m


@dataclass(frozen=True, slots=True)
class TargetCluster:
    """A spatially coherent group of detections treated as one object."""

    points: tuple[DetectedPoint, ...]
    centroid_x_m: float
    centroid_y_m: float
    centroid_z_m: float
    range_m: float
    azimuth_deg: float
    elevation_deg: float
    mean_snr_db: float
    peak_snr_db: float
    radial_velocity_mps: float
    size: TargetSize

    @property
    def point_count(self) -> int:
        return len(self.points)


@dataclass(frozen=True, slots=True)
class DetectionReport:
    """Everything a consumer needs for one frame.

    ``targets`` holds every cluster that survived gating. ``distinct_targets``
    holds the subset left after merging clusters that sit closer together than
    the array can actually resolve, and is what ``occupancy`` counts.
    """

    frame_number: int
    host_timestamp_s: float
    state: PresenceState
    targets: tuple[TargetCluster, ...] = field(default_factory=tuple)
    gated_points: tuple[DetectedPoint, ...] = field(default_factory=tuple)
    raw_point_count: int = 0
    occupancy: OccupancyState = OccupancyState.EMPTY
    distinct_targets: tuple[TargetCluster, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> TargetCluster | None:
        """Closest qualifying target, or None when nothing is in front of the sensor."""
        if not self.targets:
            return None
        return min(self.targets, key=lambda t: t.range_m)

    @property
    def distinct_count(self) -> int:
        """Number of resolvably separate objects confirmed this frame."""
        return len(self.distinct_targets)

    @property
    def multi_target(self) -> bool:
        """True while more than one distinct object is confirmed. Drives the signal line."""
        return self.occupancy is OccupancyState.MULTIPLE
