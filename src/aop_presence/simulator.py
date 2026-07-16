"""Synthetic frame source and packet encoder.

Lets the GUI, the tests, and a demo run with no EVM attached. The encoder emits
byte-exact OOB-demo packets, so it also exercises the real parser.
"""

from __future__ import annotations

import math
import random
import struct
import time
from typing import TYPE_CHECKING, Final

from .custom_types import DetectedPoint, FrameHeader, RadarFrame
from .protocol import (
    HEADER_LEN,
    MAGIC_WORD,
    PACKET_PAD_MULTIPLE,
    PLATFORM_XWR6843,
    SIDE_INFO_SCALE_DB,
    TLV_DETECTED_POINTS,
    TLV_DETECTED_POINTS_SIDE_INFO,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

SDK_VERSION_3_6: Final[int] = 0x03060000
DEFAULT_FRAME_RATE_HZ: Final[float] = 10.0
NOISE_FLOOR_DB: Final[float] = 40.0
# In non-realtime mode, a token sleep between frames prevents the producer
# thread from flooding the FrameReader queue faster than the consumer can
# drain it, which would cause consecutive pair frames to be dropped before
# the detection pipeline can accumulate its confirmation count.
_NONREALTIME_YIELD_S: Final[float] = 1e-3


def _encode_points(points: tuple[DetectedPoint, ...]) -> bytes:
    payload: bytes = b"".join(
        struct.pack("<4f", p.x_m, p.y_m, p.z_m, p.doppler_mps) for p in points
    )
    return struct.pack("<2I", TLV_DETECTED_POINTS, len(payload)) + payload


def _encode_side_info(points: tuple[DetectedPoint, ...]) -> bytes:
    payload: bytes = b"".join(
        struct.pack(
            "<2h",
            round(p.snr_db / SIDE_INFO_SCALE_DB),
            round(p.noise_db / SIDE_INFO_SCALE_DB),
        )
        for p in points
    )
    return struct.pack("<2I", TLV_DETECTED_POINTS_SIDE_INFO, len(payload)) + payload


def encode_packet(frame_number: int, points: tuple[DetectedPoint, ...]) -> bytes:
    """Build a byte-exact OOB-demo packet, padded to a 32-byte multiple."""
    tlvs: bytes = _encode_points(points) + _encode_side_info(points)
    num_tlvs: int = 2 if points else 0
    body: bytes = tlvs if points else b""
    total_len: int = HEADER_LEN + len(body)
    padding: int = (-total_len) % PACKET_PAD_MULTIPLE
    header: bytes = MAGIC_WORD + struct.pack(
        "<8I",
        SDK_VERSION_3_6,
        total_len + padding,
        PLATFORM_XWR6843,
        frame_number,
        frame_number * 1000,
        len(points),
        num_tlvs,
        0,
    )
    return header + body + b"\x00" * padding


def make_frame(frame_number: int, points: tuple[DetectedPoint, ...]) -> RadarFrame:
    """Build a RadarFrame directly, bypassing the wire format."""
    header: FrameHeader = FrameHeader(
        version=SDK_VERSION_3_6,
        total_packet_len=HEADER_LEN,
        platform=PLATFORM_XWR6843,
        frame_number=frame_number,
        time_cpu_cycles=frame_number * 1000,
        num_detected_obj=len(points),
        num_tlvs=2 if points else 0,
        subframe_number=0,
    )
    return RadarFrame(header=header, points=points, host_timestamp_s=time.monotonic())


def _target_points(
    range_m: float,
    rng: random.Random,
    count: int = 9,
    azimuth_deg: float | None = None,
) -> list[DetectedPoint]:
    """A small cloud of correlated returns, as a real body produces.

    ``azimuth_deg`` pins the bearing; leaving it None wanders randomly. The
    two-object scenario needs pinned bearings so the pair stays resolvably
    apart instead of drifting into each other.
    """
    bearing_deg: float = rng.uniform(-6.0, 6.0) if azimuth_deg is None else azimuth_deg
    azimuth_rad: float = math.radians(bearing_deg)
    points: list[DetectedPoint] = []
    for _ in range(count):
        jitter_r: float = rng.gauss(0.0, 0.06)
        jitter_x: float = rng.gauss(0.0, 0.12)
        jitter_z: float = rng.gauss(0.0, 0.25)
        radius: float = range_m + jitter_r
        points.append(
            DetectedPoint(
                x_m=radius * math.sin(azimuth_rad) + jitter_x,
                y_m=radius * math.cos(azimuth_rad),
                z_m=jitter_z,
                doppler_mps=rng.gauss(-0.4, 0.15),
                snr_db=rng.uniform(18.0, 32.0),
                noise_db=NOISE_FLOOR_DB,
            )
        )
    return points


def _clutter_points(rng: random.Random, count: int) -> list[DetectedPoint]:
    """Isolated weak detections: what an empty room actually produces."""
    return [
        DetectedPoint(
            x_m=rng.uniform(-4.0, 4.0),
            y_m=rng.uniform(0.2, 9.0),
            z_m=rng.uniform(-1.5, 1.5),
            doppler_mps=rng.gauss(0.0, 0.05),
            snr_db=rng.uniform(3.0, 11.0),
            noise_db=NOISE_FLOOR_DB,
        )
        for _ in range(count)
    ]


SCENARIO_SINGLE: Final[str] = "single"
SCENARIO_PAIR: Final[str] = "pair"
SCENARIOS: Final[tuple[str, ...]] = (SCENARIO_SINGLE, SCENARIO_PAIR)


class SimulatedSensor:
    """FrameSource producing a repeatable scripted scene with no hardware.

    single
        Empty room, then one target closing from 6 m to 1 m, then empty again.
        Frames 0-19 empty, 20-119 closing, 120-159 empty.

    pair
        The same closing target, joined at frame 45 by a second object 1.6 m
        to its left. Exercises the multi-object signal path end to end: the
        line should go HIGH shortly after frame 45 and LOW again after 105.
    """

    def __init__(
        self,
        frame_rate_hz: float = DEFAULT_FRAME_RATE_HZ,
        seed: int = 1234,
        realtime: bool = True,
        scenario: str = SCENARIO_SINGLE,
    ) -> None:
        if frame_rate_hz <= 0.0:
            raise ValueError("frame_rate_hz must be > 0")
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario must be one of {SCENARIOS}, got {scenario!r}")
        self._period_s: float = 1.0 / frame_rate_hz
        self._rng: random.Random = random.Random(seed)
        self._realtime: bool = realtime
        self._scenario: str = scenario
        self._running: bool = False

    @property
    def scenario(self) -> str:
        return self._scenario

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False

    def _second_object(self, phase: int) -> list[DetectedPoint]:
        """A second body that walks in at frame 45 and leaves at 105."""
        if self._scenario != SCENARIO_PAIR or not 45 <= phase < 105:
            return []
        return _target_points(3.2, self._rng, azimuth_deg=-24.0)

    def _points_for(self, frame_number: int) -> tuple[DetectedPoint, ...]:
        phase: int = frame_number % 160
        points: list[DetectedPoint] = _clutter_points(self._rng, self._rng.randint(0, 3))
        if 20 <= phase < 120:
            progress: float = (phase - 20) / 100.0
            points.extend(_target_points(6.0 - 5.0 * progress, self._rng, azimuth_deg=4.0))
        points.extend(self._second_object(phase))
        self._rng.shuffle(points)
        return tuple(points)

    def frames(self) -> Iterator[RadarFrame]:
        """Yield synthetic frames at the configured rate until stop()."""
        self._running = True
        frame_number: int = 0
        while self._running:
            yield make_frame(frame_number, self._points_for(frame_number))
            frame_number += 1
            if self._realtime:
                time.sleep(self._period_s)
            else:
                time.sleep(_NONREALTIME_YIELD_S)
