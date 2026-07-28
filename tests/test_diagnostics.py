"""Stage attrition reporting: does it name the gate that ate the detections?"""

from __future__ import annotations

import random

import pytest

from aop_presence.config import DetectionConfig, preset_config
from aop_presence.custom_types import DetectedPoint
from aop_presence.diagnostics import DiagnosticAccumulator, analyse_frame
from aop_presence.simulator import make_frame

LOOSE = DetectionConfig(cluster_min_points=1, cluster_eps_m=0.4, frames_to_confirm=1)


def strong(count: int, snr_db: float = 30.0) -> tuple[DetectedPoint, ...]:
    return tuple(DetectedPoint(0.05 * index, 2.0, 0.0, -0.4, snr_db) for index in range(count))


# --- per-stage counting -----------------------------------------------------


def test_clean_points_survive_every_stage() -> None:
    counts = analyse_frame(make_frame(0, strong(4)), LOOSE)
    assert counts.raw == 4
    assert counts.after_airborne == 4
    assert counts.clusters == 1


def test_snr_gate_attributed_correctly() -> None:
    counts = analyse_frame(make_frame(0, strong(4, snr_db=3.0)), DetectionConfig(min_snr_db=12.0))
    assert counts.raw == 4
    assert counts.after_snr == 0


def test_range_gate_attributed_correctly() -> None:
    far = (DetectedPoint(0.0, 40.0, 0.0, -0.4, 30.0),)
    counts = analyse_frame(make_frame(0, far), DetectionConfig(max_range_m=8.0))
    assert counts.after_snr == 1
    assert counts.after_range == 0


def test_elevation_gate_attributed_correctly() -> None:
    """A close target sits high in elevation. This is the near-field blind spot."""
    overhead = (DetectedPoint(0.0, 1.0, 0.9, -0.4, 30.0),)
    counts = analyse_frame(make_frame(0, overhead), DetectionConfig(max_elevation_deg=40.0))
    assert counts.after_range == 1
    assert counts.after_angles == 0


def test_height_gate_attributed_correctly() -> None:
    high = (DetectedPoint(0.0, 4.0, 3.0, -0.4, 30.0),)
    config = DetectionConfig(max_abs_z_m=2.0, max_elevation_deg=80.0, max_range_m=8.0)
    counts = analyse_frame(make_frame(0, high), config)
    assert counts.after_angles == 1
    assert counts.after_height == 0


def test_airborne_gate_attributed_correctly() -> None:
    config = preset_config("airborne").with_overrides(
        agl_altitude_m=2.0, sensor_pitch_deg=30.0, ground_rejection_enabled=True
    )
    ground = (DetectedPoint(0.0, 4.0, 0.0, -1.0, 25.0),)
    counts = analyse_frame(make_frame(0, ground), config)
    assert counts.after_height == 1
    assert counts.after_airborne == 0


# --- the failure that looks like an empty room ------------------------------


def sparse_person(rng: random.Random, count: int) -> tuple[DetectedPoint, ...]:
    """One to three returns off a torso, as the AOP actually delivers."""
    return tuple(
        DetectedPoint(
            rng.gauss(-0.07, 0.18), rng.gauss(1.0, 0.12), rng.gauss(0.0, 0.22), -0.48, 29.0
        )
        for _ in range(count)
    )


def accumulate(config: DetectionConfig, counts: list[int]) -> DiagnosticAccumulator:
    rng = random.Random(11)
    accumulator = DiagnosticAccumulator(config)
    for index, count in enumerate(counts):
        accumulator.update(make_frame(index, sparse_person(rng, count)), confirmed=False)
    return accumulator


SPARSE_COUNTS = [1, 1, 1, 1, 1, 2, 1, 1, 3, 1, 1, 1]


def test_starved_frames_are_counted() -> None:
    """Points clear every gate, then form no cluster. The silent failure."""
    accumulator = accumulate(
        DetectionConfig(cluster_min_points=2, cluster_eps_m=0.25), SPARSE_COUNTS
    )
    assert accumulator.starved_frames > 0
    assert accumulator.totals().after_airborne > 0


def test_starvation_produces_an_actionable_hint() -> None:
    accumulator = accumulate(
        DetectionConfig(cluster_min_points=2, cluster_eps_m=0.25), SPARSE_COUNTS
    )
    hint = accumulator.hint()
    assert "cluster_min_points" in hint
    assert "form no cluster" in hint


def test_sparse_preset_does_not_starve() -> None:
    """The whole point of the preset: single returns still make targets."""
    accumulator = accumulate(preset_config("sparse"), SPARSE_COUNTS)
    assert accumulator.starved_frames == 0


def test_no_points_at_all_blames_the_radar_not_the_gates() -> None:
    accumulator = DiagnosticAccumulator(LOOSE)
    for index in range(5):
        accumulator.update(make_frame(index, ()), confirmed=False)
    hint = accumulator.hint()
    assert "no points arrived" in hint
    assert "clutterRemoval" in hint


def test_dominant_loss_stage_is_named() -> None:
    accumulator = DiagnosticAccumulator(DetectionConfig(min_snr_db=25.0))
    for index in range(6):
        accumulator.update(make_frame(index, strong(4, snr_db=8.0)), confirmed=False)
    assert accumulator.worst_stage() == "snr"
    assert "SNR gate" in accumulator.hint()


def test_no_hint_when_nothing_stands_out() -> None:
    accumulator = DiagnosticAccumulator(LOOSE)
    for index in range(6):
        accumulator.update(make_frame(index, strong(4)), confirmed=True)
    assert accumulator.hint() == ""
    assert accumulator.worst_stage() is None


# --- rendering --------------------------------------------------------------


def test_report_shows_thresholds_next_to_losses() -> None:
    accumulator = DiagnosticAccumulator(DetectionConfig(min_snr_db=12.0))
    accumulator.update(make_frame(0, strong(3)), confirmed=True)
    report = accumulator.render()
    assert ">= 12.0 dB" in report
    assert "raw return spread" in report
    assert "clusters" in report


def test_report_survives_an_empty_run() -> None:
    assert "no data" in DiagnosticAccumulator(LOOSE).render()


def test_frames_are_counted() -> None:
    accumulator = accumulate(LOOSE, [1, 1, 1])
    assert accumulator.frames == 3


def test_starved_fraction_is_zero_before_any_frames() -> None:
    assert DiagnosticAccumulator(LOOSE).starved_fraction == pytest.approx(0.0)
