"""Tunable detection parameters, validated at construction."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pathlib import Path

# AWR6843AOP: 4 Rx x 3 Tx. The azimuth virtual aperture gives roughly this
# beamwidth, which sets the floor on any cross-range size estimate.
DEFAULT_AZIMUTH_RES_DEG: Final[float] = 15.0
DEFAULT_ELEVATION_RES_DEG: Final[float] = 30.0

# Two clusters closer together than this are treated as fragments of one
# object. Set near the azimuth cell width at typical working range: a 15 deg
# beam is roughly 0.8 m wide at 3 m, so anything tighter is not resolvable
# anyway and splitting it would be inventing a second object.
DEFAULT_MIN_SEPARATION_M: Final[float] = 0.75

# Two objects are only resolvable once they are further apart than the azimuth
# cell at their range. A 15 deg beam spans 1.32 m at 5 m, so airborne work at
# 5 m needs a separation gate near that figure, not the indoor 0.75 m.
AIRBORNE_MIN_SEPARATION_M: Final[float] = 1.35

# Sanity ceiling on a supplied or fitted platform speed. Anything past this is
# a bad telemetry frame or a bad fit, not a sUAS.
MAX_EGO_SPEED_MPS: Final[float] = 40.0


class ConfigValidationError(ValueError):
    """Raised when detection parameters are self-inconsistent."""


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Gating, clustering, and hysteresis parameters.

    Defaults are tuned for outdoor motion detection with a short minimum
    range and a moderate maximum range that favors fast-moving targets.
    """

    min_snr_db: float = 12.0
    min_range_m: float = 0.25
    max_range_m: float = 4.0
    max_azimuth_deg: float = 50.0
    max_elevation_deg: float = 40.0
    max_abs_z_m: float = 2.0
    cluster_eps_m: float = 0.35
    cluster_min_points: int = 3
    frames_to_confirm: int = 3
    frames_to_clear: int = 6
    min_target_separation_m: float = DEFAULT_MIN_SEPARATION_M
    multi_frames_to_confirm: int = 5
    multi_frames_to_clear: int = 10
    azimuth_resolution_deg: float = DEFAULT_AZIMUTH_RES_DEG
    elevation_resolution_deg: float = DEFAULT_ELEVATION_RES_DEG
    range_resolution_m: float = 0.044

    # Airborne corrections. Every one of these is inert at its default, so a
    # ground-based configuration behaves exactly as it did before they existed.
    ego_motion_enabled: bool = False
    ego_estimate_from_cloud: bool = False
    ego_forward_mps: float = 0.0
    min_relative_velocity_mps: float = 0.0
    ground_rejection_enabled: bool = False
    agl_altitude_m: float = 0.0
    sensor_pitch_deg: float = 0.0
    ground_clearance_m: float = 0.5

    def __post_init__(self) -> None:
        self._validate_ranges()
        self._validate_counts()
        self._validate_airborne()

    def _validate_ranges(self) -> None:
        if self.min_range_m < 0.0:
            raise ConfigValidationError("min_range_m must be >= 0")
        if self.max_range_m <= self.min_range_m:
            raise ConfigValidationError("max_range_m must exceed min_range_m")
        if not 0.0 < self.max_azimuth_deg <= 90.0:
            raise ConfigValidationError("max_azimuth_deg must be in (0, 90]")
        if not 0.0 < self.max_elevation_deg <= 90.0:
            raise ConfigValidationError("max_elevation_deg must be in (0, 90]")
        if self.cluster_eps_m <= 0.0:
            raise ConfigValidationError("cluster_eps_m must be > 0")
        if self.min_target_separation_m <= 0.0:
            raise ConfigValidationError("min_target_separation_m must be > 0")
        if self.min_target_separation_m < self.cluster_eps_m:
            raise ConfigValidationError(
                "min_target_separation_m must be >= cluster_eps_m; a separation "
                "below the clustering radius cannot split anything DBSCAN merged"
            )

    def _validate_counts(self) -> None:
        if self.cluster_min_points < 1:
            raise ConfigValidationError("cluster_min_points must be >= 1")
        if self.frames_to_confirm < 1:
            raise ConfigValidationError("frames_to_confirm must be >= 1")
        if self.frames_to_clear < 1:
            raise ConfigValidationError("frames_to_clear must be >= 1")
        if self.multi_frames_to_confirm < 1:
            raise ConfigValidationError("multi_frames_to_confirm must be >= 1")
        if self.multi_frames_to_clear < 1:
            raise ConfigValidationError("multi_frames_to_clear must be >= 1")

    def _validate_airborne(self) -> None:
        if abs(self.ego_forward_mps) > MAX_EGO_SPEED_MPS:
            raise ConfigValidationError(f"ego_forward_mps must be within +/-{MAX_EGO_SPEED_MPS}")
        if self.min_relative_velocity_mps < 0.0:
            raise ConfigValidationError("min_relative_velocity_mps must be >= 0")
        if not -90.0 <= self.sensor_pitch_deg <= 90.0:
            raise ConfigValidationError("sensor_pitch_deg must be in [-90, 90]")
        if self.ground_clearance_m < 0.0:
            raise ConfigValidationError("ground_clearance_m must be >= 0")
        if self.agl_altitude_m < 0.0:
            raise ConfigValidationError("agl_altitude_m must be >= 0")
        if self.ground_rejection_enabled and self.agl_altitude_m <= 0.0:
            raise ConfigValidationError(
                "ground_rejection_enabled requires agl_altitude_m > 0; without a "
                "height there is no ground plane to reject against"
            )

    @property
    def airborne_corrections_active(self) -> bool:
        """True when any platform-motion or ground gate is doing work."""
        return (
            self.ego_motion_enabled
            or self.ground_rejection_enabled
            or self.min_relative_velocity_mps > 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, **overrides: Any) -> DetectionConfig:
        """Return a copy with fields replaced; validation reruns on the copy."""
        return replace(self, **overrides)


# Tuned for a sUAS at 5 m working range against the airborne_5m chirp profile,
# which resolves 6.5 cm in range and +/-10.15 m/s in velocity at 20 Hz.
#
# Differences from the indoor defaults, and why:
#   max_range_m           5.0 caps the working range as required.
#   min_snr_db           15.0 outdoor clutter raises the noise floor.
#   max_abs_z_m           6.0 a tilted airborne mount puts real targets well
#                             off the sensor horizontal plane.
#   min_target_separation 1.35 the azimuth cell at 5 m, not the indoor 0.75 m.
#   frames_to_confirm     4/8 at 20 Hz this is the same latency as 3/6 at 10 Hz.
#   range_resolution_m  0.125 derived from the airborne_5m chirp.
#   ego_motion            on   platform speed fitted from the static field.
AIRBORNE_5M: Final[DetectionConfig] = DetectionConfig(
    min_snr_db=15.0,
    min_range_m=0.3,
    max_range_m=5.0,
    max_azimuth_deg=50.0,
    max_elevation_deg=40.0,
    max_abs_z_m=6.0,
    cluster_eps_m=0.5,
    cluster_min_points=3,
    frames_to_confirm=4,
    frames_to_clear=8,
    min_target_separation_m=AIRBORNE_MIN_SEPARATION_M,
    multi_frames_to_confirm=8,
    multi_frames_to_clear=16,
    range_resolution_m=0.1246,
    ego_motion_enabled=True,
    ego_estimate_from_cloud=True,
)

INDOOR_DEFAULT: Final[DetectionConfig] = DetectionConfig()

# Tuned on hardware during live outdoor testing, not derived. Tighter cluster
# radius and a faster latch than indoor, because outdoor targets move and the
# clutter that survives gating is sparser than a room full of furniture.
# Mirrors configs/detection_gates.json.
OUTDOOR_GROUND: Final[DetectionConfig] = DetectionConfig(
    min_snr_db=8.0,
    min_range_m=0.25,
    max_range_m=8.0,
    max_azimuth_deg=55.0,
    max_elevation_deg=55.0,
    max_abs_z_m=2.0,
    cluster_eps_m=0.25,
    cluster_min_points=2,
    frames_to_confirm=2,
    frames_to_clear=5,
    multi_frames_to_confirm=2,
    multi_frames_to_clear=5,
)

PRESETS: Final[dict[str, DetectionConfig]] = {
    "indoor": INDOOR_DEFAULT,
    "outdoor": OUTDOOR_GROUND,
    "airborne": AIRBORNE_5M,
}


def preset_config(name: str) -> DetectionConfig:
    """Return a named detection preset."""
    preset: DetectionConfig | None = PRESETS.get(name)
    if preset is None:
        raise ConfigValidationError(f"Unknown preset {name!r}; expected one of {sorted(PRESETS)}")
    return preset


def load_detection_config(path: Path) -> DetectionConfig:
    """Load a DetectionConfig from JSON. Unknown keys are rejected."""
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(f"Cannot read detection config {path}: {exc}") from exc
    known: set[str] = set(DetectionConfig.__dataclass_fields__)
    unknown: set[str] = set(raw) - known
    if unknown:
        raise ConfigValidationError(f"Unknown config keys: {sorted(unknown)}")
    return DetectionConfig(**raw)
