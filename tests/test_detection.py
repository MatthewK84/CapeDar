"""Gating, clustering, hysteresis, sizing, and the assembled pipeline."""

from __future__ import annotations

import pytest

from aop_presence.clustering import cluster_points
from aop_presence.config import ConfigValidationError, DetectionConfig
from aop_presence.filters import gate_points, is_valid_point
from aop_presence.pipeline import DetectionPipeline
from aop_presence.presence import PresenceTracker
from aop_presence.simulator import make_frame
from aop_presence.sizing import cross_range_cell_m, estimate_size
from aop_presence.custom_types import DetectedPoint, PresenceState

CONFIG = DetectionConfig()


def strong(x: float, y: float, z: float = 0.0) -> DetectedPoint:
    return DetectedPoint(x, y, z, doppler_mps=-0.2, snr_db=25.0, noise_db=40.0)


def blob(count: int = 5, y: float = 2.0) -> tuple[DetectedPoint, ...]:
    return tuple(strong(0.02 * i, y + 0.02 * i, 0.03 * i) for i in range(count))


class TestGating:
    def test_weak_point_is_rejected(self) -> None:
        assert not is_valid_point(DetectedPoint(0.0, 2.0, 0.0, 0.0, snr_db=5.0), CONFIG)

    def test_point_behind_sensor_is_rejected(self) -> None:
        assert not is_valid_point(strong(0.0, -2.0), CONFIG)

    def test_point_beyond_max_range_is_rejected(self) -> None:
        assert not is_valid_point(strong(0.0, 20.0), CONFIG)

    def test_point_too_close_is_rejected(self) -> None:
        assert not is_valid_point(strong(0.0, 0.05), CONFIG)

    def test_point_outside_azimuth_fov_is_rejected(self) -> None:
        assert not is_valid_point(strong(5.0, 1.0), CONFIG)

    def test_point_in_front_and_strong_is_accepted(self) -> None:
        assert is_valid_point(strong(0.1, 2.0), CONFIG)

    def test_gate_points_filters_mixed_cloud(self) -> None:
        cloud = (strong(0.0, 2.0), DetectedPoint(0.0, 2.0, 0.0, 0.0, snr_db=1.0))
        assert len(gate_points(cloud, CONFIG)) == 1


class TestClustering:
    def test_isolated_points_are_discarded(self) -> None:
        scattered = (strong(0.0, 1.0), strong(3.0, 5.0), strong(-2.0, 7.0))
        assert cluster_points(scattered, eps_m=0.35, min_points=3) == []

    def test_dense_blob_forms_one_cluster(self) -> None:
        clusters = cluster_points(blob(), eps_m=0.35, min_points=3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_two_separated_blobs_form_two_clusters(self) -> None:
        clusters = cluster_points(blob(5, 2.0) + blob(5, 6.0), eps_m=0.35, min_points=3)
        assert len(clusters) == 2

    def test_clusters_sorted_nearest_first(self) -> None:
        clusters = cluster_points(blob(5, 6.0) + blob(5, 2.0), eps_m=0.35, min_points=3)
        assert clusters[0][0].range_m < clusters[1][0].range_m

    def test_empty_input_returns_no_clusters(self) -> None:
        assert cluster_points((), eps_m=0.35, min_points=3) == []

    def test_large_cloud_does_not_exhaust_the_stack(self) -> None:
        chain = tuple(strong(0.0, 1.0 + 0.05 * i) for i in range(400))
        clusters = cluster_points(chain, eps_m=0.35, min_points=3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 400


class TestPresenceTracker:
    def test_starts_absent(self) -> None:
        assert PresenceTracker(3, 6).state is PresenceState.ABSENT

    def test_single_hit_does_not_confirm(self) -> None:
        tracker = PresenceTracker(3, 6)
        assert tracker.update(True) is PresenceState.ABSENT

    def test_confirms_after_required_hits(self) -> None:
        tracker = PresenceTracker(3, 6)
        for _ in range(3):
            tracker.update(True)
        assert tracker.is_present

    def test_single_miss_does_not_clear(self) -> None:
        tracker = PresenceTracker(3, 2)
        for _ in range(3):
            tracker.update(True)
        assert tracker.update(False) is PresenceState.PRESENT

    def test_clears_after_required_misses(self) -> None:
        tracker = PresenceTracker(3, 2)
        for _ in range(3):
            tracker.update(True)
        tracker.update(False)
        assert tracker.update(False) is PresenceState.ABSENT

    def test_intermittent_hits_never_confirm(self) -> None:
        tracker = PresenceTracker(3, 6)
        for _ in range(20):
            tracker.update(True)
            tracker.update(False)
        assert not tracker.is_present

    def test_reset_returns_to_absent(self) -> None:
        tracker = PresenceTracker(1, 1)
        tracker.update(True)
        tracker.reset()
        assert tracker.state is PresenceState.ABSENT

    def test_rejects_invalid_counts(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            PresenceTracker(0, 3)


class TestSizing:
    def test_cell_grows_with_range(self) -> None:
        near = cross_range_cell_m(1.0, 15.0)
        far = cross_range_cell_m(5.0, 15.0)
        assert far > near
        assert near == pytest.approx(0.263, abs=0.01)

    def test_cell_is_zero_at_zero_range(self) -> None:
        assert cross_range_cell_m(0.0, 15.0) == 0.0

    def test_tiny_target_is_floored_at_resolution_cell(self) -> None:
        size = estimate_size(blob(5, 4.0), CONFIG)
        assert size.resolution_limited
        assert size.width_m == pytest.approx(cross_range_cell_m(4.0, 15.0), abs=0.02)

    def test_wide_target_exceeds_the_floor(self) -> None:
        wide = tuple(strong(-0.9 + 0.3 * i, 1.0, 0.0) for i in range(7))
        size = estimate_size(wide, CONFIG)
        assert size.width_m == pytest.approx(1.8, abs=0.01)

    def test_depth_never_below_range_resolution(self) -> None:
        size = estimate_size(blob(3, 2.0), CONFIG)
        assert size.depth_m >= CONFIG.range_resolution_m

    def test_empty_cluster_is_zero_sized(self) -> None:
        assert estimate_size((), CONFIG).width_m == 0.0


class TestPipeline:
    def test_empty_room_reports_absent(self) -> None:
        pipeline = DetectionPipeline(CONFIG)
        for number in range(10):
            report = pipeline.process(make_frame(number, ()))
        assert report.state is PresenceState.ABSENT
        assert report.primary is None

    def test_noise_only_never_confirms_presence(self) -> None:
        weak = tuple(DetectedPoint(0.3 * i, 2.0, 0.0, 0.0, snr_db=4.0) for i in range(8))
        pipeline = DetectionPipeline(CONFIG)
        for number in range(30):
            report = pipeline.process(make_frame(number, weak))
        assert report.state is PresenceState.ABSENT
        assert report.raw_point_count == 8
        assert report.gated_points == ()

    def test_sustained_target_confirms_and_reports_range(self) -> None:
        pipeline = DetectionPipeline(CONFIG)
        for number in range(5):
            report = pipeline.process(make_frame(number, blob(6, 3.0)))
        assert report.state is PresenceState.PRESENT
        target = report.primary
        assert target is not None
        assert target.range_m == pytest.approx(3.05, abs=0.15)

    def test_targets_are_withheld_until_confirmed(self) -> None:
        pipeline = DetectionPipeline(CONFIG)
        report = pipeline.process(make_frame(0, blob(6, 3.0)))
        assert report.state is PresenceState.ABSENT
        assert report.targets == ()
        assert len(report.gated_points) == 6

    def test_target_leaving_eventually_clears(self) -> None:
        pipeline = DetectionPipeline(CONFIG)
        for number in range(5):
            pipeline.process(make_frame(number, blob(6, 3.0)))
        for number in range(5, 20):
            report = pipeline.process(make_frame(number, ()))
        assert report.state is PresenceState.ABSENT

    def test_nearest_of_two_targets_is_primary(self) -> None:
        pipeline = DetectionPipeline(CONFIG)
        for number in range(5):
            report = pipeline.process(make_frame(number, blob(5, 5.0) + blob(5, 2.0)))
        assert len(report.targets) == 2
        primary = report.primary
        assert primary is not None
        assert primary.range_m < 3.0


class TestConfigValidation:
    def test_rejects_inverted_range_window(self) -> None:
        with pytest.raises(ConfigValidationError):
            DetectionConfig(min_range_m=5.0, max_range_m=1.0)

    def test_rejects_impossible_azimuth(self) -> None:
        with pytest.raises(ConfigValidationError):
            DetectionConfig(max_azimuth_deg=120.0)

    def test_rejects_zero_confirm_frames(self) -> None:
        with pytest.raises(ConfigValidationError):
            DetectionConfig(frames_to_confirm=0)

    def test_overrides_are_revalidated(self) -> None:
        with pytest.raises(ConfigValidationError):
            DetectionConfig().with_overrides(max_range_m=-1.0)
