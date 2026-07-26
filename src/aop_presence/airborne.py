"""Corrections that only matter once the sensor leaves the ground.

A tripod-mounted radar sees a static world. A radar bolted to a sUAS does not,
and three assumptions baked into ground-based presence detection break at once.

Ego-motion
    Every static object acquires an apparent radial velocity equal to the
    projection of the platform velocity onto the line of sight. Ground,
    fenceposts, and parked vehicles all start moving. Doppler stops separating
    movers from clutter until the platform component is removed.

Ground clutter
    Pointing anywhere with a downward component turns the ground into an
    extended reflector that fills the range gates. Indoors the floor is outside
    the elevation beam; airborne it is the dominant scatterer.

Vibration
    Rotor and airframe vibration adds a low-amplitude, roughly symmetric
    Doppler spread around every return, which inflates apparent velocity noise.

Nothing here is enabled by default. Every gate in this module is inert unless
the corresponding DetectionConfig field turns it on, so ground-based behaviour
is bit-for-bit unchanged.

Axes follow the sensor convention used throughout the package: +x right,
+y boresight, +z up. Radial velocity is range rate, so negative means closing.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from .custom_types import EgoEstimate

if TYPE_CHECKING:
    from .config import DetectionConfig
    from .custom_types import DetectedPoint

# A cosine fit needs the point cloud spread across bearings. Points bunched at
# one bearing give a near-singular fit that reports confident nonsense.
MIN_EGO_FIT_POINTS: Final[int] = 6
MIN_BEARING_SPREAD: Final[float] = 0.15
MAX_PLAUSIBLE_EGO_MPS: Final[float] = 40.0


def boresight_cosine(point: DetectedPoint) -> float:
    """Cosine of the angle between the boresight axis and the point direction."""
    range_m: float = point.range_m
    if range_m <= 0.0:
        return 0.0
    return point.y_m / range_m


def expected_static_doppler_mps(point: DetectedPoint, forward_mps: float) -> float:
    """Range rate a static point would show while the platform flies forward.

    Flying forward shortens the range to anything ahead, so the expected value
    is negative for positive forward speed.
    """
    return -forward_mps * boresight_cosine(point)


def relative_doppler_mps(point: DetectedPoint, forward_mps: float) -> float:
    """Measured range rate with the platform contribution removed."""
    return point.doppler_mps - expected_static_doppler_mps(point, forward_mps)


def _bearing_spread(cosines: list[float]) -> float:
    if len(cosines) < 2:
        return 0.0
    return max(cosines) - min(cosines)


def _least_squares_forward(points: tuple[DetectedPoint, ...]) -> float:
    """Slope of doppler against boresight cosine, forced through the origin."""
    numerator: float = 0.0
    denominator: float = 0.0
    for point in points:
        cosine: float = boresight_cosine(point)
        numerator += point.doppler_mps * cosine
        denominator += cosine * cosine
    if denominator <= 0.0:
        return 0.0
    return -numerator / denominator


def _fit_residual(points: tuple[DetectedPoint, ...], forward_mps: float) -> float:
    """Root-mean-square disagreement between the fit and the measurements."""
    if not points:
        return 0.0
    total: float = math.fsum(relative_doppler_mps(p, forward_mps) ** 2 for p in points)
    return math.sqrt(total / len(points))


def estimate_forward_speed(points: tuple[DetectedPoint, ...]) -> EgoEstimate:
    """Recover platform forward speed from the dominant static field.

    Static returns satisfy ``doppler = -v * cos(bearing)``. Fitting that cosine
    across the cloud recovers ``v`` without an autopilot feed. The estimate is
    only marked trusted when enough points span enough bearings, because a
    cloud dominated by one mover will happily fit a speed that is not real.

    Prefer a telemetry-supplied speed when one exists. This exists so the
    package degrades usefully when it does not.
    """
    if len(points) < MIN_EGO_FIT_POINTS:
        return EgoEstimate(0.0, len(points), 0.0, trusted=False)
    cosines: list[float] = [boresight_cosine(point) for point in points]
    if _bearing_spread(cosines) < MIN_BEARING_SPREAD:
        return EgoEstimate(0.0, len(points), 0.0, trusted=False)
    forward: float = _least_squares_forward(points)
    if abs(forward) > MAX_PLAUSIBLE_EGO_MPS:
        return EgoEstimate(0.0, len(points), 0.0, trusted=False)
    residual: float = _fit_residual(points, forward)
    return EgoEstimate(forward, len(points), residual, trusted=True)


def resolve_forward_speed(
    points: tuple[DetectedPoint, ...], config: DetectionConfig
) -> EgoEstimate:
    """Choose between a supplied platform speed and one fitted from the cloud."""
    if not config.ego_motion_enabled:
        return EgoEstimate(0.0, len(points), 0.0, trusted=False)
    if not config.ego_estimate_from_cloud:
        return EgoEstimate(config.ego_forward_mps, len(points), 0.0, trusted=True)
    estimate: EgoEstimate = estimate_forward_speed(points)
    if estimate.trusted:
        return estimate
    return EgoEstimate(config.ego_forward_mps, len(points), 0.0, trusted=False)


def height_above_ground_m(point: DetectedPoint, agl_m: float, pitch_deg: float) -> float:
    """Height of a point above the ground plane, in metres.

    ``pitch_deg`` is the sensor tilt below horizontal, so a nose-down mount is
    positive. With the sensor ``agl_m`` above flat ground, a point on the
    ground returns approximately zero.
    """
    pitch_rad: float = math.radians(pitch_deg)
    vertical_offset: float = point.z_m * math.cos(pitch_rad) - point.y_m * math.sin(pitch_rad)
    return agl_m + vertical_offset


def passes_ground_clearance(point: DetectedPoint, config: DetectionConfig) -> bool:
    """Reject returns sitting on or below the ground plane."""
    if not config.ground_rejection_enabled:
        return True
    height: float = height_above_ground_m(point, config.agl_altitude_m, config.sensor_pitch_deg)
    return height >= config.ground_clearance_m


def passes_relative_motion(
    point: DetectedPoint, config: DetectionConfig, forward_mps: float
) -> bool:
    """Reject returns that move exactly as the static world should.

    Turning this on trades away static targets to buy clutter rejection. A
    parked vehicle and the ground it sits on both look static, so a threshold
    above zero detects movers only.
    """
    if config.min_relative_velocity_mps <= 0.0:
        return True
    return abs(relative_doppler_mps(point, forward_mps)) >= config.min_relative_velocity_mps


def apply_airborne_gates(
    points: tuple[DetectedPoint, ...], config: DetectionConfig
) -> tuple[tuple[DetectedPoint, ...], EgoEstimate]:
    """Run the platform-motion and ground gates over an already-gated cloud.

    Returns the surviving points and the ego estimate used, so callers can log
    what the correction believed about the platform.
    """
    ego: EgoEstimate = resolve_forward_speed(points, config)
    if not config.ground_rejection_enabled and config.min_relative_velocity_mps <= 0.0:
        return points, ego
    survivors: tuple[DetectedPoint, ...] = tuple(
        point
        for point in points
        if passes_ground_clearance(point, config)
        and passes_relative_motion(point, config, ego.forward_mps)
    )
    return survivors, ego
