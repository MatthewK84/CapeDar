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


class ConfigValidationError(ValueError):
    """Raised when detection parameters are self-inconsistent."""


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Gating, clustering, and hysteresis parameters.

    Defaults are tuned for indoor human presence at 0.3 m to 8 m on the
    AWR6843AOPEVM running the SDK 3.6 Out-of-Box demo.
    """

    min_snr_db: float = 12.0
    min_range_m: float = 0.25
    max_range_m: float = 8.0
    max_azimuth_deg: float = 50.0
    max_elevation_deg: float = 40.0
    max_abs_z_m: float = 2.0
    cluster_eps_m: float = 0.35
    cluster_min_points: int = 3
    frames_to_confirm: int = 3
    frames_to_clear: int = 6
    azimuth_resolution_deg: float = DEFAULT_AZIMUTH_RES_DEG
    elevation_resolution_deg: float = DEFAULT_ELEVATION_RES_DEG
    range_resolution_m: float = 0.044

    def __post_init__(self) -> None:
        self._validate_ranges()
        self._validate_counts()

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

    def _validate_counts(self) -> None:
        if self.cluster_min_points < 1:
            raise ConfigValidationError("cluster_min_points must be >= 1")
        if self.frames_to_confirm < 1:
            raise ConfigValidationError("frames_to_confirm must be >= 1")
        if self.frames_to_clear < 1:
            raise ConfigValidationError("frames_to_clear must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, **overrides: Any) -> DetectionConfig:
        """Return a copy with fields replaced; validation reruns on the copy."""
        return replace(self, **overrides)


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
