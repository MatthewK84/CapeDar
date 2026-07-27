"""Find out which stage is eating the detections.

A detector that reports nothing looks identical to an empty room. That
ambiguity is expensive in the field, where the difference between "the radar
sees nothing" and "the radar sees plenty and my gates threw it away" is the
difference between a wasted afternoon and a two-second fix.

This module runs the same predicates the pipeline uses, in the same order, and
counts survivors after each one. It also reports the spread of the raw returns,
so a gate can be compared against the data it is filtering rather than against
a guess.

Nothing here changes detection behaviour. It only observes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from .airborne import apply_airborne_gates
from .clustering import cluster_points
from .filters import passes_angles, passes_height, passes_range, passes_snr

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import DetectionConfig
    from .custom_types import DetectedPoint, RadarFrame

# A stage that removes at least this share of what reached it is worth naming.
NOTABLE_LOSS_FRACTION: Final[float] = 0.25

# Frames where points cleared every gate but still formed no cluster. Above
# this share, clustering is the bottleneck, not the gates. This is the failure
# that looks exactly like an empty room: the returns are there and the operator
# never sees them.
STARVED_FRAME_FRACTION: Final[float] = 0.2


@dataclass(frozen=True, slots=True)
class StageCounts:
    """Survivors after each gate, in pipeline order."""

    raw: int
    after_snr: int
    after_range: int
    after_angles: int
    after_height: int
    after_airborne: int
    clusters: int

    @property
    def stages(self) -> tuple[tuple[str, int, int], ...]:
        """(label, entering, leaving) for each stage, for rendering."""
        return (
            ("snr", self.raw, self.after_snr),
            ("range", self.after_snr, self.after_range),
            ("angles", self.after_range, self.after_angles),
            ("height", self.after_angles, self.after_height),
            ("airborne", self.after_height, self.after_airborne),
        )


def _survivors(
    points: tuple[DetectedPoint, ...],
    config: DetectionConfig,
    predicate: Callable[[DetectedPoint, DetectionConfig], bool],
) -> tuple[DetectedPoint, ...]:
    return tuple(point for point in points if predicate(point, config))


def analyse_frame(frame: RadarFrame, config: DetectionConfig) -> StageCounts:
    """Re-run the gate chain, counting what each stage removes."""
    raw: tuple[DetectedPoint, ...] = frame.points
    snr: tuple[DetectedPoint, ...] = _survivors(raw, config, passes_snr)
    ranged: tuple[DetectedPoint, ...] = _survivors(snr, config, passes_range)
    angled: tuple[DetectedPoint, ...] = _survivors(ranged, config, passes_angles)
    tall: tuple[DetectedPoint, ...] = _survivors(angled, config, passes_height)
    flown, _ = apply_airborne_gates(tall, config)
    groups = cluster_points(flown, config.cluster_eps_m, config.cluster_min_points)
    return StageCounts(
        raw=len(raw),
        after_snr=len(snr),
        after_range=len(ranged),
        after_angles=len(angled),
        after_height=len(tall),
        after_airborne=len(flown),
        clusters=len(groups),
    )


@dataclass
class Spread:
    """Running min, median, and max of one measurement across raw returns."""

    label: str
    unit: str
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    def render(self) -> str:
        if not self.values:
            return f"  {self.label:<8} no data"
        low: float = min(self.values)
        mid: float = statistics.median(self.values)
        high: float = max(self.values)
        return f"  {self.label:<8} {low:8.2f} {mid:8.2f} {high:8.2f}  {self.unit}"


def _empty_spreads() -> dict[str, Spread]:
    return {
        "range": Spread("range", "m"),
        "azimuth": Spread("azimuth", "deg"),
        "elevation": Spread("elev", "deg"),
        "snr": Spread("snr", "dB"),
        "doppler": Spread("doppler", "m/s"),
    }


class DiagnosticAccumulator:
    """Totals attrition and raw-return spread across many frames."""

    def __init__(self, config: DetectionConfig) -> None:
        self._config: DetectionConfig = config
        self._frames: int = 0
        self._totals: list[int] = [0] * 7
        self._confirmed_frames: int = 0
        self._starved_frames: int = 0
        self._spreads: dict[str, Spread] = _empty_spreads()

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def starved_frames(self) -> int:
        """Frames whose points cleared every gate but formed no cluster."""
        return self._starved_frames

    @property
    def starved_fraction(self) -> float:
        if self._frames == 0:
            return 0.0
        return self._starved_frames / self._frames

    def update(self, frame: RadarFrame, confirmed: bool) -> None:
        """Fold one frame into the running totals."""
        counts: StageCounts = analyse_frame(frame, self._config)
        self._frames += 1
        self._confirmed_frames += int(confirmed)
        if counts.after_airborne > 0 and counts.clusters == 0:
            self._starved_frames += 1
        observed: tuple[int, ...] = (
            counts.raw,
            counts.after_snr,
            counts.after_range,
            counts.after_angles,
            counts.after_height,
            counts.after_airborne,
            counts.clusters,
        )
        self._totals = [running + new for running, new in zip(self._totals, observed, strict=True)]
        self._record_spread(frame)

    def _record_spread(self, frame: RadarFrame) -> None:
        for point in frame.points:
            self._spreads["range"].add(point.range_m)
            self._spreads["azimuth"].add(point.azimuth_deg)
            self._spreads["elevation"].add(point.elevation_deg)
            self._spreads["snr"].add(point.snr_db)
            self._spreads["doppler"].add(point.doppler_mps)

    def totals(self) -> StageCounts:
        return StageCounts(*self._totals)

    def worst_stage(self) -> str | None:
        """Name the stage removing the largest share of what reaches it."""
        worst: str | None = None
        worst_loss: float = NOTABLE_LOSS_FRACTION
        for label, entering, leaving in self.totals().stages:
            if entering <= 0:
                continue
            loss: float = (entering - leaving) / entering
            if loss > worst_loss:
                worst_loss = loss
                worst = label
        return worst

    def hint(self) -> str:
        """One actionable sentence, or an empty string when nothing stands out."""
        totals: StageCounts = self.totals()
        if totals.raw == 0:
            return (
                "HINT no points arrived at all. The radar is not detecting, not the gates. "
                "Check the profile loaded, and that clutterRemoval is 0 for static targets."
            )
        if self.starved_fraction > STARVED_FRAME_FRACTION:
            return (
                f"HINT {self._starved_frames} of {self._frames} frames had points clear "
                f"every gate but form no cluster. Clustering is the bottleneck, not the "
                f"gates. Lower cluster_min_points (now {self._config.cluster_min_points}) "
                f"or raise cluster_eps_m (now {self._config.cluster_eps_m:.2f} m). "
                f"Sparse returns need cluster_min_points 1."
            )
        return _stage_hint(self.worst_stage(), self._config)

    def render(self) -> str:
        """The whole report as one printable block."""
        totals: StageCounts = self.totals()
        lines: list[str] = [
            f"DIAG frames={self._frames} raw={totals.raw} "
            f"confirmed_frames={self._confirmed_frames} starved={self._starved_frames}"
        ]
        lines.extend(_render_stages(totals, self._config))
        lines.append(f"  {'clusters':<8} {totals.clusters:>8} formed")
        lines.append("raw return spread (min / median / max)")
        lines.extend(spread.render() for spread in self._spreads.values())
        hint: str = self.hint()
        if hint:
            lines.append(hint)
        return "\n".join(lines)


def _render_stages(totals: StageCounts, config: DetectionConfig) -> list[str]:
    lines: list[str] = []
    for label, entering, leaving in totals.stages:
        loss: str = _loss_text(entering, leaving)
        lines.append(
            f"  {label:<8} {entering:>6} -> {leaving:<6} {loss:<10} {_limit(label, config)}"
        )
    return lines


def _loss_text(entering: int, leaving: int) -> str:
    if entering <= 0:
        return "-"
    return f"-{100.0 * (entering - leaving) / entering:.0f}%"


def _limit(label: str, config: DetectionConfig) -> str:
    """Show the threshold each stage is applying, so it can be judged."""
    limits: dict[str, str] = {
        "snr": f">= {config.min_snr_db:.1f} dB",
        "range": f"{config.min_range_m:.2f} to {config.max_range_m:.2f} m",
        "angles": f"az <= {config.max_azimuth_deg:.0f}, el <= {config.max_elevation_deg:.0f} deg",
        "height": f"|z| <= {config.max_abs_z_m:.2f} m",
        "airborne": "on" if config.airborne_corrections_active else "inactive",
    }
    return limits.get(label, "")


def _stage_hint(stage: str | None, config: DetectionConfig) -> str:
    if stage is None:
        return ""
    hints: dict[str, str] = {
        "snr": f"HINT the SNR gate is the biggest loss. Try lowering min_snr_db below {config.min_snr_db:.1f}.",
        "range": f"HINT the range gate is the biggest loss. Window is {config.min_range_m:.2f} to {config.max_range_m:.2f} m.",
        "angles": (
            "HINT the angle gate is the biggest loss. Close targets sit at high elevation, "
            f"so raise max_elevation_deg above {config.max_elevation_deg:.0f}."
        ),
        "height": f"HINT the height gate is the biggest loss. Raise max_abs_z_m above {config.max_abs_z_m:.2f} m.",
        "airborne": "HINT the airborne gates are the biggest loss. Check --agl, --pitch, and --movers-only.",
    }
    return hints.get(stage, "")
