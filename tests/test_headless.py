"""Headless detection event and heartbeat output."""

from __future__ import annotations

from io import StringIO

from aop_presence.config import DetectionConfig
from aop_presence.custom_types import DetectedPoint, DetectionReport, PresenceState
from aop_presence.headless import HeadlessMonitor
from aop_presence.pipeline import DetectionPipeline
from aop_presence.simulator import make_frame


def report(frame: int, timestamp: float, state: PresenceState) -> DetectionReport:
    return DetectionReport(
        frame_number=frame,
        host_timestamp_s=timestamp,
        state=state,
        raw_point_count=2,
    )


def test_monitor_prints_rate_limited_status() -> None:
    stream = StringIO()
    monitor = HeadlessMonitor(status_interval_s=1.0, stream=stream)
    monitor.update(report(1, 10.0, PresenceState.ABSENT))
    monitor.update(report(2, 10.5, PresenceState.ABSENT))
    monitor.update(report(3, 11.0, PresenceState.ABSENT))
    output = stream.getvalue()
    assert output.count("STATUS") == 2
    assert "frame=1 state=ABSENT" in output
    assert "frame=3 state=ABSENT" in output


def test_monitor_prints_clear_transition() -> None:
    stream = StringIO()
    monitor = HeadlessMonitor(stream=stream)
    pipeline = DetectionPipeline(DetectionConfig(cluster_min_points=1, frames_to_confirm=1))
    point = DetectedPoint(0.0, 1.0, 0.0, 0.1, snr_db=20.0)
    detected = pipeline.process(make_frame(8, (point,)))
    monitor.update(detected)
    monitor.update(report(9, 20.0, PresenceState.ABSENT))
    output = stream.getvalue()
    assert "DETECTED frame=8 range=1.00m" in output
    assert "CLEARED frame=9" in output


def test_monitor_rejects_nonpositive_interval() -> None:
    try:
        HeadlessMonitor(status_interval_s=0.0)
    except ValueError as exc:
        assert "must be > 0" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
