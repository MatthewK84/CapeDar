"""Density clustering of gated points.

Second of the three anti-phantom stages: an isolated CFAR detection with no
neighbours is noise, not an object. DBSCAN is implemented iteratively with an
explicit queue -- no recursion, so stack depth is constant regardless of how
many points arrive (Principle 6).
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .custom_types import DetectedPoint

UNVISITED: Final[int] = -1
NOISE: Final[int] = -2


def squared_distance(left: DetectedPoint, right: DetectedPoint) -> float:
    return (left.x_m - right.x_m) ** 2 + (left.y_m - right.y_m) ** 2 + (left.z_m - right.z_m) ** 2


def find_neighbours(
    points: tuple[DetectedPoint, ...], index: int, eps_squared: float
) -> list[int]:
    """Indices within eps of points[index], inclusive of index itself."""
    origin: DetectedPoint = points[index]
    return [
        other
        for other in range(len(points))
        if squared_distance(origin, points[other]) <= eps_squared
    ]


def _expand_cluster(
    points: tuple[DetectedPoint, ...],
    labels: list[int],
    seeds: list[int],
    cluster_id: int,
    eps_squared: float,
    min_points: int,
) -> None:
    """Grow one cluster breadth-first. Mutates ``labels`` in place."""
    queue: deque[int] = deque(seeds)
    while queue:
        current: int = queue.popleft()
        if labels[current] == NOISE:
            labels[current] = cluster_id
        if labels[current] != UNVISITED:
            continue
        labels[current] = cluster_id
        neighbours: list[int] = find_neighbours(points, current, eps_squared)
        if len(neighbours) < min_points:
            continue
        queue.extend(index for index in neighbours if labels[index] == UNVISITED)


def cluster_points(
    points: tuple[DetectedPoint, ...], eps_m: float, min_points: int
) -> list[tuple[DetectedPoint, ...]]:
    """Group points into clusters. Points with too few neighbours are dropped."""
    if not points:
        return []
    eps_squared: float = eps_m * eps_m
    labels: list[int] = [UNVISITED] * len(points)
    next_cluster_id: int = 0
    for index in range(len(points)):
        if labels[index] != UNVISITED:
            continue
        neighbours: list[int] = find_neighbours(points, index, eps_squared)
        if len(neighbours) < min_points:
            labels[index] = NOISE
            continue
        labels[index] = next_cluster_id
        _expand_cluster(points, labels, neighbours, next_cluster_id, eps_squared, min_points)
        next_cluster_id += 1
    return _collect(points, labels, next_cluster_id)


def _collect(
    points: tuple[DetectedPoint, ...], labels: list[int], cluster_count: int
) -> list[tuple[DetectedPoint, ...]]:
    """Bucket points by label, discarding noise, nearest cluster first."""
    buckets: list[list[DetectedPoint]] = [[] for _ in range(cluster_count)]
    for index, label in enumerate(labels):
        if label >= 0:
            buckets[label].append(points[index])
    clusters: list[tuple[DetectedPoint, ...]] = [tuple(b) for b in buckets if b]
    clusters.sort(key=_nearest_range)
    return clusters


def _nearest_range(cluster: tuple[DetectedPoint, ...]) -> float:
    return min(point.range_m for point in cluster)


def mean_of(values: list[float]) -> float:
    """Arithmetic mean; returns 0.0 for an empty input rather than raising."""
    if not values:
        return 0.0
    return math.fsum(values) / len(values)
