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

from .protocol import (
    HEADER_LEN,
    MAGIC_WORD,
    PACKET_PAD_MULTIPLE,
    PLATFORM_XWR6843,
    SIDE_INFO_SCALE_DB,
    TLV_DETECTED_POINTS,
    TLV_DETECTED_POINTS_SIDE_INFO,
)
from .custom_types import DetectedPoint, FrameHeader, RadarFrame

if TYPE_CHECKING:
    from collections.abc import Iterator

SDK_VERSION_3_6: Final[int] = 0x03060000
DEFAULT_FRAME_RATE_HZ: Final[float] = 10.0
NOISE_FLOOR_DB: Final[float] = 40.0


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


def _target_points(range_m: float, rng: random.Random, count: int = 9) -> list[DetectedPoint]:
    """A small cloud of correlated returns, as a real body produces."""
    azimuth_rad: float = math.radians(rng.uniform(-6.0, 6.0))
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


class SimulatedSensor:
    """FrameSource that walks a target from 6 m to 1 m, then out of the room.

    Frames 0-19 are empty room, 20-119 a closing target, 120+ empty again.
    """

    def __init__(
        self, frame_rate_hz: float = DEFAULT_FRAME_RATE_HZ, seed: int = 1234, realtime: bool = True
    ) -> None:
        if frame_rate_hz <= 0.0:
            raise ValueError("frame_rate_hz must be > 0")
        self._period_s: float = 1.0 / frame_rate_hz
        self._rng: random.Random = random.Random(seed)
        self._realtime: bool = realtime
        self._running: bool = False

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False

    def _points_for(self, frame_number: int) -> tuple[DetectedPoint, ...]:
        phase: int = frame_number % 160
        points: list[DetectedPoint] = _clutter_points(self._rng, self._rng.randint(0, 3))
        if 20 <= phase < 120:
            progress: float = (phase - 20) / 100.0
            points.extend(_target_points(6.0 - 5.0 * progress, self._rng))
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
