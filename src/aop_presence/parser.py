"""Parse the Out-of-Box demo UART byte stream into RadarFrame objects."""

from __future__ import annotations

import logging
import struct
import time

from .protocol import (
    HEADER_LEN,
    HEADER_STRUCT,
    MAGIC_WORD,
    MAX_PACKET_LEN,
    MAX_POINTS_PER_FRAME,
    POINT_LEN,
    POINT_STRUCT,
    SIDE_INFO_LEN,
    SIDE_INFO_SCALE_DB,
    SIDE_INFO_STRUCT,
    TLV_DETECTED_POINTS,
    TLV_DETECTED_POINTS_SIDE_INFO,
    TLV_HEADER_LEN,
    TLV_HEADER_STRUCT,
    FramingError,
    PayloadError,
)
from .types import DetectedPoint, FrameHeader, RadarFrame

logger: logging.Logger = logging.getLogger(__name__)

MAX_BUFFER_BYTES: int = 4 * MAX_PACKET_LEN


def parse_header(packet: bytes) -> FrameHeader:
    """Decode the 40-byte frame header (magic word already stripped)."""
    if len(packet) < HEADER_LEN:
        raise FramingError(f"Header needs {HEADER_LEN} bytes, got {len(packet)}")
    fields: tuple[int, ...] = struct.unpack_from(HEADER_STRUCT, packet, len(MAGIC_WORD))
    return FrameHeader(
        version=fields[0],
        total_packet_len=fields[1],
        platform=fields[2],
        frame_number=fields[3],
        time_cpu_cycles=fields[4],
        num_detected_obj=fields[5],
        num_tlvs=fields[6],
        subframe_number=fields[7],
    )


def split_tlvs(packet: bytes, num_tlvs: int) -> list[tuple[int, bytes]]:
    """Return (type, payload) pairs. Stops early on a malformed length."""
    tlvs: list[tuple[int, bytes]] = []
    offset: int = HEADER_LEN
    for _ in range(num_tlvs):
        if offset + TLV_HEADER_LEN > len(packet):
            logger.debug("Truncated TLV header at offset %d", offset)
            return tlvs
        tlv_type, tlv_len = struct.unpack_from(TLV_HEADER_STRUCT, packet, offset)
        offset += TLV_HEADER_LEN
        end: int = offset + int(tlv_len)
        if int(tlv_len) < 0 or end > len(packet):
            logger.debug("TLV type %d declares %d bytes, packet too short", tlv_type, tlv_len)
            return tlvs
        tlvs.append((int(tlv_type), packet[offset:end]))
        offset = end
    return tlvs


def parse_detected_points(payload: bytes) -> list[DetectedPoint]:
    """Decode TLV type 1: x/y/z/doppler float32 per point."""
    if len(payload) % POINT_LEN != 0:
        raise PayloadError(f"Point payload {len(payload)} not a multiple of {POINT_LEN}")
    count: int = len(payload) // POINT_LEN
    if count > MAX_POINTS_PER_FRAME:
        raise PayloadError(f"Point count {count} exceeds cap {MAX_POINTS_PER_FRAME}")
    return [
        DetectedPoint(
            x_m=float(values[0]),
            y_m=float(values[1]),
            z_m=float(values[2]),
            doppler_mps=float(values[3]),
        )
        for values in struct.iter_unpack(POINT_STRUCT, payload)
    ]


def parse_side_info(payload: bytes) -> list[tuple[float, float]]:
    """Decode TLV type 7: (snr_db, noise_db) per point, native units of 0.1 dB."""
    if len(payload) % SIDE_INFO_LEN != 0:
        raise PayloadError(f"Side-info payload {len(payload)} not a multiple of {SIDE_INFO_LEN}")
    return [
        (float(snr) * SIDE_INFO_SCALE_DB, float(noise) * SIDE_INFO_SCALE_DB)
        for snr, noise in struct.iter_unpack(SIDE_INFO_STRUCT, payload)
    ]


def attach_side_info(
    points: list[DetectedPoint], side_info: list[tuple[float, float]]
) -> list[DetectedPoint]:
    """Merge SNR/noise into points. Extra or missing side-info entries are ignored."""
    if not side_info:
        return points
    pairs: int = min(len(points), len(side_info))
    if pairs != len(points):
        logger.debug("Side-info count %d != point count %d", len(side_info), len(points))
    merged: list[DetectedPoint] = []
    for index in range(pairs):
        snr_db, noise_db = side_info[index]
        merged.append(
            DetectedPoint(
                x_m=points[index].x_m,
                y_m=points[index].y_m,
                z_m=points[index].z_m,
                doppler_mps=points[index].doppler_mps,
                snr_db=snr_db,
                noise_db=noise_db,
            )
        )
    merged.extend(points[pairs:])
    return merged


def parse_packet(packet: bytes, host_timestamp_s: float) -> RadarFrame:
    """Turn one complete magic-word-aligned packet into a RadarFrame."""
    header: FrameHeader = parse_header(packet)
    points: list[DetectedPoint] = []
    side_info: list[tuple[float, float]] = []
    for tlv_type, payload in split_tlvs(packet, header.num_tlvs):
        if tlv_type == TLV_DETECTED_POINTS:
            points = parse_detected_points(payload)
        elif tlv_type == TLV_DETECTED_POINTS_SIDE_INFO:
            side_info = parse_side_info(payload)
    return RadarFrame(
        header=header,
        points=tuple(attach_side_info(points, side_info)),
        host_timestamp_s=host_timestamp_s,
    )


class FrameAssembler:
    """Re-assembles frames from arbitrarily chunked serial reads.

    Holds a private byte buffer; resynchronises on the magic word after any
    corruption, and drops the buffer if it grows past MAX_BUFFER_BYTES.
    """

    def __init__(self, max_buffer_bytes: int = MAX_BUFFER_BYTES) -> None:
        self._buffer: bytearray = bytearray()
        self._max_buffer_bytes: int = max_buffer_bytes
        self._dropped_frames: int = 0

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    def feed(self, chunk: bytes) -> list[RadarFrame]:
        """Append bytes and return every complete frame now available."""
        self._buffer.extend(chunk)
        self._enforce_buffer_cap()
        frames: list[RadarFrame] = []
        while True:
            packet: bytes | None = self._take_packet()
            if packet is None:
                return frames
            frame: RadarFrame | None = self._safe_parse(packet)
            if frame is not None:
                frames.append(frame)

    def _safe_parse(self, packet: bytes) -> RadarFrame | None:
        try:
            return parse_packet(packet, time.monotonic())
        except (FramingError, PayloadError, struct.error) as exc:
            self._dropped_frames += 1
            logger.warning("Dropping malformed frame: %s", exc)
            return None

    def _enforce_buffer_cap(self) -> None:
        if len(self._buffer) <= self._max_buffer_bytes:
            return
        logger.warning("Buffer over %d bytes; discarding stale data", self._max_buffer_bytes)
        del self._buffer[: -self._max_buffer_bytes]

    def _take_packet(self) -> bytes | None:
        """Pop one complete packet from the buffer head, or None if incomplete."""
        start: int = self._buffer.find(MAGIC_WORD)
        if start < 0:
            self._trim_to_partial_magic()
            return None
        if start > 0:
            del self._buffer[:start]
        if len(self._buffer) < HEADER_LEN:
            return None
        total_len: int = struct.unpack_from("<I", self._buffer, len(MAGIC_WORD) + 4)[0]
        if total_len < HEADER_LEN or total_len > MAX_PACKET_LEN:
            self._dropped_frames += 1
            del self._buffer[: len(MAGIC_WORD)]
            return None
        if len(self._buffer) < total_len:
            return None
        packet: bytes = bytes(self._buffer[:total_len])
        del self._buffer[:total_len]
        return packet

    def _trim_to_partial_magic(self) -> None:
        """Keep only the tail that could still be the start of a magic word."""
        keep: int = len(MAGIC_WORD) - 1
        if len(self._buffer) > keep:
            del self._buffer[:-keep]
