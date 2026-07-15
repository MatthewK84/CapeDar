"""End-to-end signal-line behaviour: the runner, the watchdog, and fail-safe exits."""

from __future__ import annotations

import json
import time
from io import StringIO
from typing import TYPE_CHECKING

import pytest

from aop_presence.config import DetectionConfig
from aop_presence.custom_types import DetectedPoint
from aop_presence.gpio import NullSink
from aop_presence.headless import HeadlessOptions, HeadlessRunner
from aop_presence.reader import FrameReader
from aop_presence.simulator import SCENARIO_PAIR, SimulatedSensor, make_frame

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aop_presence.custom_types import RadarFrame

FAST_CONFIG = DetectionConfig(
    frames_to_confirm=1, multi_frames_to_confirm=2, multi_frames_to_clear=2
)


def two_bodies() -> tuple[DetectedPoint, ...]:
    return tuple(
        DetectedPoint(x + dx, 4.0 + dy, 0.0, -0.3, 25.0)
        for x in (-0.9, 0.9)
        for dx in (-0.04, 0.04)
        for dy in (-0.04, 0.04)
    )


def one_body() -> tuple[DetectedPoint, ...]:
    return tuple(
        DetectedPoint(dx, 4.0 + dy, 0.0, -0.3, 25.0)
        for dx in (-0.04, 0.04)
        for dy in (-0.04, 0.04)
    )


class ScriptedSource:
    """FrameSource that replays a fixed list of point sets, then stops."""

    def __init__(self, script: list[tuple[DetectedPoint, ...]]) -> None:
        self._script = script
        self.closed = False

    def frames(self) -> Iterator[RadarFrame]:
        for number, points in enumerate(self._script):
            yield make_frame(number, points)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class RecordingSink:
    """Captures every signal transition for assertion."""

    def __init__(self) -> None:
        self.transitions: list[bool] = []
        self.asserted = False
        self.closed = False

    def set_state(self, asserted: bool) -> None:
        if asserted != self.asserted:
            self.transitions.append(asserted)
        self.asserted = asserted

    def close(self) -> None:
        self.closed = True


def run(script: list[tuple[DetectedPoint, ...]], sink: RecordingSink) -> str:
    stream = StringIO()
    runner = HeadlessRunner(
        ScriptedSource(script),
        FAST_CONFIG,
        HeadlessOptions(status_interval_s=99.0),
        sink,
        stream,
    )
    runner.run()
    return stream.getvalue()


def test_signal_asserts_for_two_objects() -> None:
    sink = RecordingSink()
    run([two_bodies()] * 5, sink)
    # The trailing False is the shutdown de-assert, not a detection event.
    assert sink.transitions[0] is True


def test_signal_stays_low_for_one_object() -> None:
    sink = RecordingSink()
    run([one_body()] * 8, sink)
    assert sink.transitions == []


def test_signal_stays_low_in_an_empty_room() -> None:
    sink = RecordingSink()
    run([()] * 20, sink)
    assert sink.transitions == []


def test_signal_clears_when_second_object_leaves() -> None:
    sink = RecordingSink()
    run([two_bodies()] * 5 + [one_body()] * 5, sink)
    assert sink.transitions == [True, False]


def test_signal_is_low_after_the_source_ends() -> None:
    """Fail-safe: the line must never outlive the process."""
    sink = RecordingSink()
    run([two_bodies()] * 5, sink)
    assert not sink.asserted
    assert sink.closed


def test_signal_is_low_after_source_raises() -> None:
    """A dying radar must drop the line, not latch it high."""

    class ExplodingSource:
        def frames(self) -> Iterator[RadarFrame]:
            for number in range(4):
                yield make_frame(number, two_bodies())
            raise OSError("serial port vanished")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    sink = RecordingSink()
    runner = HeadlessRunner(ExplodingSource(), FAST_CONFIG, HeadlessOptions(), sink, StringIO())
    assert runner.run() == 1
    assert not sink.asserted
    assert sink.closed


def test_transitions_are_announced() -> None:
    sink = RecordingSink()
    output = run([two_bodies()] * 5 + [one_body()] * 5, sink)
    assert "MULTI " in output
    assert "objects=2" in output
    assert "signal=HIGH" in output
    assert "MULTI-CLEARED" in output
    assert "signal=LOW" in output


def test_json_mode_emits_one_record_per_frame() -> None:
    stream = StringIO()
    runner = HeadlessRunner(
        ScriptedSource([two_bodies()] * 4),
        FAST_CONFIG,
        HeadlessOptions(as_json=True),
        NullSink(),
        stream,
    )
    runner.run()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(records) == 4
    assert records[-1]["multi_target"] is True
    assert records[-1]["distinct_count"] == 2
    assert records[-1]["primary"]["range_m"] > 0.0


def test_stale_stream_forces_the_signal_low() -> None:
    """A sensor that stops talking without closing must not leave the line high."""

    class StallingSource:
        def frames(self) -> Iterator[RadarFrame]:
            for number in range(4):
                yield make_frame(number, two_bodies())
            while True:
                time.sleep(0.05)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    sink = RecordingSink()
    stream = StringIO()
    runner = HeadlessRunner(
        StallingSource(),
        FAST_CONFIG,
        HeadlessOptions(stale_timeout_s=0.3),
        sink,
        stream,
    )

    def stop_soon() -> None:
        time.sleep(1.2)
        runner.request_stop()

    import threading

    threading.Thread(target=stop_soon, daemon=True).start()
    runner.run()
    assert sink.transitions == [True, False]
    assert "STALE" in stream.getvalue()


def test_reader_returns_none_when_quiet() -> None:
    class SilentSource:
        def frames(self) -> Iterator[RadarFrame]:
            while True:
                time.sleep(0.05)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    reader = FrameReader(SilentSource())
    reader.start()
    assert reader.next_frame(0.15) is None
    reader.stop()


def test_reader_drops_oldest_when_consumer_stalls() -> None:
    reader = FrameReader(ScriptedSource([one_body()] * 50), max_frames=4)
    reader.start()
    time.sleep(0.3)
    assert reader.dropped_frames > 0


def test_reader_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError, match="max_frames must be >= 1"):
        FrameReader(ScriptedSource([]), max_frames=0)


def test_reader_cannot_start_twice() -> None:
    reader = FrameReader(ScriptedSource([]))
    reader.start()
    with pytest.raises(RuntimeError, match="already started"):
        reader.start()
    reader.stop()


def test_pair_scenario_drives_the_signal_high() -> None:
    """The hardware-free demo must exercise the real signal path."""
    sensor = SimulatedSensor(realtime=False, scenario=SCENARIO_PAIR)
    sink = RecordingSink()
    runner = HeadlessRunner(sensor, DetectionConfig(), HeadlessOptions(), sink, StringIO())

    def stop_after_frames() -> None:
        time.sleep(0.8)
        runner.request_stop()

    import threading

    threading.Thread(target=stop_after_frames, daemon=True).start()
    runner.run()
    assert True in sink.transitions
    assert not sink.asserted


def test_single_scenario_never_drives_the_signal_high() -> None:
    sensor = SimulatedSensor(realtime=False)
    sink = RecordingSink()
    runner = HeadlessRunner(sensor, DetectionConfig(), HeadlessOptions(), sink, StringIO())

    def stop_after_frames() -> None:
        time.sleep(0.8)
        runner.request_stop()

    import threading

    threading.Thread(target=stop_after_frames, daemon=True).start()
    runner.run()
    assert sink.transitions == []


def test_simulator_rejects_unknown_scenario() -> None:
    with pytest.raises(ValueError, match="scenario must be one of"):
        SimulatedSensor(scenario="crowd")
