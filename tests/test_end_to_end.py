"""Simulated walk-in through the wire format, parser, and pipeline."""

from __future__ import annotations

from itertools import islice

from aop_presence.config import DetectionConfig
from aop_presence.parser import FrameAssembler
from aop_presence.pipeline import DetectionPipeline
from aop_presence.sensor import FrameSource
from aop_presence.simulator import SimulatedSensor, encode_packet
from aop_presence.custom_types import DetectionReport, PresenceState, RadarFrame


def collect(frame_count: int) -> list[DetectionReport]:
    """Run frames through encode -> parse -> pipeline, exactly as hardware would."""
    source = SimulatedSensor(realtime=False, seed=7)
    assembler = FrameAssembler()
    pipeline = DetectionPipeline(DetectionConfig())
    reports: list[DetectionReport] = []
    for frame in islice(source.frames(), frame_count):
        packet = encode_packet(frame.header.frame_number, frame.points)
        reports.extend(pipeline.process(parsed) for parsed in assembler.feed(packet))
    source.stop()
    return reports


def test_simulator_satisfies_frame_source_protocol() -> None:
    assert isinstance(SimulatedSensor(realtime=False), FrameSource)


def test_empty_room_phase_reports_no_presence() -> None:
    reports = collect(150)
    assert all(r.state is PresenceState.ABSENT for r in reports[:18])


def test_target_is_detected_while_present() -> None:
    reports = collect(150)
    assert any(r.state is PresenceState.PRESENT for r in reports[30:110])


def test_target_range_closes_over_time() -> None:
    reports = collect(150)
    ranges = [r.primary.range_m for r in reports[30:110] if r.primary is not None]
    assert len(ranges) > 40
    assert ranges[0] > ranges[-1] + 2.0


def test_presence_clears_after_target_leaves() -> None:
    reports = collect(150)
    assert reports[-1].state is PresenceState.ABSENT
    assert reports[-1].primary is None


def test_clutter_alone_never_yields_a_target() -> None:
    absent = [r for r in collect(150) if r.state is PresenceState.ABSENT]
    assert all(r.targets == () for r in absent)


def test_source_yields_radar_frames() -> None:
    source = SimulatedSensor(realtime=False)
    frames = list(islice(source.frames(), 3))
    source.stop()
    assert all(isinstance(f, RadarFrame) for f in frames)
    assert [f.header.frame_number for f in frames] == [0, 1, 2]
