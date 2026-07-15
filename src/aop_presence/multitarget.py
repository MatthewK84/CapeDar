"""Deciding that there is genuinely more than one object out there.

Counting DBSCAN clusters is not the same as counting objects, and treating it
that way produces a signal line that chatters. Two failure modes dominate:

Fragmentation
    One person returns detections from torso, arms, and head. Density gaps
    between those blobs split them into two or three clusters. Counting
    clusters would report a crowd where one person stands.

Merging
    Two people standing shoulder to shoulder at 6 m fall inside a single
    azimuth resolution cell. No amount of post-processing recovers them.

This module addresses fragmentation with a separation gate: clusters closer
together than the array can resolve are folded into the strongest of them.
Merging is a physical limit and is not addressed here; see the range/separation
table in the README.

Separation is measured in the ground plane. Head and feet returns from one
person share (x, y) and differ only in z, so a 3-D metric would keep them
apart; a horizontal metric correctly folds them together.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .custom_types import OccupancyState
from .presence import Hysteresis

if TYPE_CHECKING:
    from .custom_types import TargetCluster

MULTI_TARGET_THRESHOLD: int = 2


def horizontal_separation_m(first: TargetCluster, second: TargetCluster) -> float:
    """Ground-plane distance between two cluster centroids, ignoring height."""
    return math.hypot(
        first.centroid_x_m - second.centroid_x_m,
        first.centroid_y_m - second.centroid_y_m,
    )


def _is_clear_of(candidate: TargetCluster, kept: list[TargetCluster], min_sep_m: float) -> bool:
    """True when the candidate is resolvably far from everything already kept."""
    return all(horizontal_separation_m(candidate, existing) >= min_sep_m for existing in kept)


def resolve_distinct(
    targets: tuple[TargetCluster, ...], min_separation_m: float
) -> tuple[TargetCluster, ...]:
    """Fold clusters closer than ``min_separation_m`` into their strongest member.

    Strongest-first ordering makes the result stable: the cluster the radar is
    most confident about anchors each object, and its weaker fragments are
    absorbed rather than the other way round.
    """
    if min_separation_m <= 0.0:
        raise ValueError("min_separation_m must be > 0")
    ordered: list[TargetCluster] = sorted(targets, key=lambda t: t.peak_snr_db, reverse=True)
    kept: list[TargetCluster] = []
    for candidate in ordered:
        if _is_clear_of(candidate, kept, min_separation_m):
            kept.append(candidate)
    kept.sort(key=lambda t: t.range_m)
    return tuple(kept)


class OccupancyTracker:
    """Latches MULTIPLE only after the multi-object condition persists.

    State is per-instance; construct one tracker per sensor stream.
    """

    def __init__(self, frames_to_confirm: int, frames_to_clear: int) -> None:
        self._hysteresis: Hysteresis = Hysteresis(frames_to_confirm, frames_to_clear)
        self._state: OccupancyState = OccupancyState.EMPTY

    @property
    def state(self) -> OccupancyState:
        return self._state

    @property
    def is_multiple(self) -> bool:
        return self._state is OccupancyState.MULTIPLE

    def reset(self) -> None:
        self._hysteresis.reset()
        self._state = OccupancyState.EMPTY

    def update(self, distinct_count: int) -> OccupancyState:
        """Advance one frame with this frame's distinct object count."""
        if distinct_count < 0:
            raise ValueError("distinct_count must be >= 0")
        latched: bool = self._hysteresis.update(distinct_count >= MULTI_TARGET_THRESHOLD)
        self._state = self._classify(latched, distinct_count)
        return self._state

    def _classify(self, latched: bool, distinct_count: int) -> OccupancyState:
        if latched:
            return OccupancyState.MULTIPLE
        if distinct_count > 0:
            return OccupancyState.SINGLE
        return OccupancyState.EMPTY
