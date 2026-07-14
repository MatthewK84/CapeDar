"""Frame -> DetectionReport. The whole detection policy lives here."""

from __future__ import annotations

import math

from .clustering import cluster_points, mean_of
from .config import DetectionConfig
from .filters import gate_points
from .presence import PresenceTracker
from .sizing import estimate_size
from .types import DetectedPoint, DetectionReport, PresenceState, RadarFrame, TargetCluster


def build_cluster(points: tuple[DetectedPoint, ...], config: DetectionConfig) -> TargetCluster:
    """Summarise a group of points as a single target."""
    count: int = len(points)
    if count == 0:
        raise ValueError("Cannot summarise an empty cluster")
    centroid_x: float = mean_of([point.x_m for point in points])
    centroid_y: float = mean_of([point.y_m for point in points])
    centroid_z: float = mean_of([point.z_m for point in points])
    ground_range: float = math.hypot(centroid_x, centroid_y)
    snrs: list[float] = [point.snr_db for point in points]
    return TargetCluster(
        points=points,
        centroid_x_m=centroid_x,
        centroid_y_m=centroid_y,
        centroid_z_m=centroid_z,
        range_m=math.sqrt(centroid_x**2 + centroid_y**2 + centroid_z**2),
        azimuth_deg=math.degrees(math.atan2(centroid_x, centroid_y)),
        elevation_deg=math.degrees(math.atan2(centroid_z, ground_range)),
        mean_snr_db=mean_of(snrs),
        peak_snr_db=max(snrs),
        radial_velocity_mps=mean_of([point.doppler_mps for point in points]),
        size=estimate_size(points, config),
    )


def find_targets(
    gated: tuple[DetectedPoint, ...], config: DetectionConfig
) -> tuple[TargetCluster, ...]:
    """Cluster gated points and summarise each surviving cluster."""
    groups: list[tuple[DetectedPoint, ...]] = cluster_points(
        gated, config.cluster_eps_m, config.cluster_min_points
    )
    return tuple(build_cluster(group, config) for group in groups)


class DetectionPipeline:
    """Stateful per-stream processor. One instance per sensor."""

    def __init__(self, config: DetectionConfig | None = None) -> None:
        self._config: DetectionConfig = config or DetectionConfig()
        self._tracker: PresenceTracker = PresenceTracker(
            self._config.frames_to_confirm, self._config.frames_to_clear
        )

    @property
    def config(self) -> DetectionConfig:
        return self._config

    def reset(self) -> None:
        self._tracker.reset()

    def process(self, frame: RadarFrame) -> DetectionReport:
        """Run one frame through gating, clustering, and hysteresis."""
        gated: tuple[DetectedPoint, ...] = gate_points(frame.points, self._config)
        targets: tuple[TargetCluster, ...] = find_targets(gated, self._config)
        state: PresenceState = self._tracker.update(len(targets) > 0)
        reported: tuple[TargetCluster, ...] = targets if state is PresenceState.PRESENT else ()
        return DetectionReport(
            frame_number=frame.header.frame_number,
            host_timestamp_s=frame.host_timestamp_s,
            state=state,
            targets=reported,
            gated_points=gated,
            raw_point_count=len(frame.points),
        )
