"""Multi-object resolution, occupancy hysteresis, and the signal sink."""

from __future__ import annotations

import pytest

from aop_presence.config import ConfigValidationError, DetectionConfig
from aop_presence.custom_types import DetectedPoint, OccupancyState, TargetCluster
from aop_presence.gpio import GpioError, GpioSettings, NullSink, create_signal_sink
from aop_presence.multitarget import (
    OccupancyTracker,
    horizontal_separation_m,
    resolve_distinct,
)
from aop_presence.pipeline import DetectionPipeline, build_cluster
from aop_presence.simulator import make_frame

CONFIG = DetectionConfig()


def blob(x_m: float, y_m: float, snr_db: float = 25.0, z_m: float = 0.0) -> TargetCluster:
    """A tight cluster of returns centred on (x, y, z)."""
    points = tuple(
        DetectedPoint(x_m + dx, y_m + dy, z_m, -0.3, snr_db)
        for dx in (-0.04, 0.04)
        for dy in (-0.04, 0.04)
    )
    return build_cluster(points, CONFIG)


def test_separation_ignores_height() -> None:
    """Head and feet returns share (x, y); a 3-D metric would split one person."""
    head = blob(0.0, 4.0, z_m=0.8)
    feet = blob(0.0, 4.0, z_m=-0.8)
    assert horizontal_separation_m(head, feet) == pytest.approx(0.0, abs=1e-6)


def test_two_separated_objects_stay_distinct() -> None:
    left, right = blob(-0.8, 4.0), blob(0.8, 4.0)
    assert len(resolve_distinct((left, right), 0.75)) == 2


def test_fragments_of_one_object_collapse() -> None:
    """DBSCAN can split one body into blobs; those must not read as a crowd."""
    torso, arm = blob(0.0, 4.0, snr_db=28.0), blob(0.3, 4.0, snr_db=15.0)
    distinct = resolve_distinct((torso, arm), 0.75)
    assert len(distinct) == 1
    assert distinct[0].peak_snr_db == pytest.approx(28.0)


def test_strongest_cluster_anchors_the_object() -> None:
    """Ordering by SNR keeps the result stable frame to frame."""
    weak, strong = blob(0.0, 4.0, snr_db=14.0), blob(0.2, 4.0, snr_db=30.0)
    distinct = resolve_distinct((weak, strong), 0.75)
    assert distinct[0].peak_snr_db == pytest.approx(30.0)


def test_resolve_distinct_is_order_independent() -> None:
    a, b, c = blob(-1.5, 4.0, 20.0), blob(0.0, 4.0, 30.0), blob(1.5, 4.0, 25.0)
    assert len(resolve_distinct((a, b, c), 0.75)) == len(resolve_distinct((c, a, b), 0.75)) == 3


def test_empty_input_yields_no_objects() -> None:
    assert resolve_distinct((), 0.75) == ()


def test_resolve_distinct_rejects_bad_separation() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        resolve_distinct((blob(0.0, 4.0),), 0.0)


def test_occupancy_needs_sustained_evidence() -> None:
    tracker = OccupancyTracker(frames_to_confirm=5, frames_to_clear=10)
    for _ in range(4):
        assert tracker.update(2) is OccupancyState.SINGLE
    assert tracker.update(2) is OccupancyState.MULTIPLE


def test_occupancy_survives_a_dropout() -> None:
    """A single frame losing one target must not drop the signal line."""
    tracker = OccupancyTracker(frames_to_confirm=3, frames_to_clear=6)
    for _ in range(3):
        tracker.update(2)
    tracker.update(1)
    assert tracker.is_multiple


def test_occupancy_clears_after_sustained_absence() -> None:
    tracker = OccupancyTracker(frames_to_confirm=3, frames_to_clear=6)
    for _ in range(3):
        tracker.update(2)
    for _ in range(5):
        tracker.update(1)
    assert tracker.is_multiple
    assert tracker.update(1) is OccupancyState.SINGLE


def test_occupancy_reports_empty_with_no_targets() -> None:
    tracker = OccupancyTracker(3, 6)
    assert tracker.update(0) is OccupancyState.EMPTY


def test_occupancy_reset_returns_to_empty() -> None:
    tracker = OccupancyTracker(1, 1)
    tracker.update(2)
    assert tracker.is_multiple
    tracker.reset()
    assert tracker.state is OccupancyState.EMPTY


def test_occupancy_rejects_negative_count() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        OccupancyTracker(3, 6).update(-1)


def test_config_rejects_separation_below_cluster_radius() -> None:
    """A separation under eps cannot split what DBSCAN already merged."""
    with pytest.raises(ConfigValidationError, match="min_target_separation_m"):
        DetectionConfig(cluster_eps_m=0.5, min_target_separation_m=0.2)


def test_config_rejects_nonpositive_separation() -> None:
    with pytest.raises(ConfigValidationError, match="min_target_separation_m"):
        DetectionConfig(min_target_separation_m=0.0)


def _two_body_points() -> tuple[DetectedPoint, ...]:
    return tuple(
        DetectedPoint(x + dx, 4.0 + dy, 0.0, -0.3, 25.0)
        for x in (-0.9, 0.9)
        for dx in (-0.04, 0.04)
        for dy in (-0.04, 0.04)
    )


def test_pipeline_asserts_multi_target_only_after_confirmation() -> None:
    config = DetectionConfig(frames_to_confirm=1, multi_frames_to_confirm=3)
    pipeline = DetectionPipeline(config)
    points = _two_body_points()
    first = pipeline.process(make_frame(0, points))
    assert first.distinct_count == 2
    assert not first.multi_target
    pipeline.process(make_frame(1, points))
    third = pipeline.process(make_frame(2, points))
    assert third.multi_target
    assert third.occupancy is OccupancyState.MULTIPLE


def test_pipeline_reports_no_multi_target_for_one_object() -> None:
    config = DetectionConfig(frames_to_confirm=1, multi_frames_to_confirm=1)
    pipeline = DetectionPipeline(config)
    points = tuple(
        DetectedPoint(dx, 4.0 + dy, 0.0, -0.3, 25.0)
        for dx in (-0.04, 0.04)
        for dy in (-0.04, 0.04)
    )
    for number in range(5):
        report = pipeline.process(make_frame(number, points))
    assert report.distinct_count == 1
    assert not report.multi_target


def test_empty_room_never_asserts_the_signal() -> None:
    """The whole point: silence in, silence out."""
    pipeline = DetectionPipeline(DetectionConfig())
    for number in range(30):
        report = pipeline.process(make_frame(number, ()))
        assert not report.multi_target
        assert report.occupancy is OccupancyState.EMPTY


def test_pipeline_reset_clears_occupancy() -> None:
    config = DetectionConfig(frames_to_confirm=1, multi_frames_to_confirm=1)
    pipeline = DetectionPipeline(config)
    assert pipeline.process(make_frame(0, _two_body_points())).multi_target
    pipeline.reset()
    assert not pipeline.process(make_frame(1, ())).multi_target


def test_null_sink_tracks_state_without_hardware() -> None:
    sink = NullSink()
    initial: bool = sink.asserted
    sink.set_state(True)
    after_set: bool = sink.asserted
    sink.close()
    after_close: bool = sink.asserted
    assert (initial, after_set, after_close) == (False, True, False)


def test_gpio_off_mode_returns_null_sink() -> None:
    assert isinstance(create_signal_sink("off"), NullSink)


def test_gpio_auto_degrades_without_hardware() -> None:
    """This is what lets the same command line run under Windows PowerShell."""
    assert isinstance(create_signal_sink("auto"), NullSink)


def test_gpio_on_mode_fails_loudly_without_hardware() -> None:
    """An explicitly requested line must never silently no-op."""
    with pytest.raises(GpioError):
        create_signal_sink("on")


def test_gpio_rejects_unknown_mode() -> None:
    with pytest.raises(GpioError, match="Unknown gpio mode"):
        create_signal_sink("maybe")


def test_gpio_settings_reject_empty_pin() -> None:
    with pytest.raises(GpioError, match="non-empty"):
        GpioSettings(pin="")


def test_gpio_settings_default_to_physical_pin_11() -> None:
    assert GpioSettings().pin == "BOARD11"
    assert GpioSettings().active_high is True
