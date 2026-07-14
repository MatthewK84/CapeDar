"""Byte-level framing and TLV parsing."""

from __future__ import annotations

import pytest

from aop_presence.parser import (
    FrameAssembler,
    attach_side_info,
    parse_detected_points,
    parse_header,
    parse_packet,
    parse_side_info,
)
from aop_presence.protocol import (
    HEADER_LEN,
    MAGIC_WORD,
    PLATFORM_XWR6843,
    FramingError,
    PayloadError,
)
from aop_presence.simulator import encode_packet
from aop_presence.types import DetectedPoint


def make_points() -> tuple[DetectedPoint, ...]:
    return (
        DetectedPoint(0.10, 2.00, 0.05, -0.30, snr_db=21.5, noise_db=40.0),
        DetectedPoint(-0.05, 2.10, 0.15, -0.25, snr_db=18.2, noise_db=40.0),
    )


def test_encode_parse_round_trip() -> None:
    packet = encode_packet(frame_number=42, points=make_points())
    frame = parse_packet(packet, host_timestamp_s=1.0)
    assert frame.header.frame_number == 42
    assert frame.header.platform == PLATFORM_XWR6843
    assert frame.header.num_detected_obj == 2
    assert len(frame.points) == 2
    assert frame.points[0].x_m == pytest.approx(0.10, abs=1e-6)
    assert frame.points[0].snr_db == pytest.approx(21.5, abs=0.05)


def test_packet_is_padded_to_32_byte_multiple() -> None:
    packet = encode_packet(frame_number=1, points=make_points())
    assert len(packet) % 32 == 0
    assert packet.startswith(MAGIC_WORD)


def test_empty_frame_parses_with_no_points() -> None:
    frame = parse_packet(encode_packet(frame_number=7, points=()), host_timestamp_s=0.0)
    assert frame.points == ()
    assert frame.header.num_tlvs == 0


def test_parse_header_rejects_short_buffer() -> None:
    with pytest.raises(FramingError):
        parse_header(MAGIC_WORD + b"\x00" * 4)


def test_parse_detected_points_rejects_ragged_payload() -> None:
    with pytest.raises(PayloadError):
        parse_detected_points(b"\x00" * 17)


def test_parse_side_info_rejects_ragged_payload() -> None:
    with pytest.raises(PayloadError):
        parse_side_info(b"\x00" * 3)


def test_side_info_scales_by_tenth_db() -> None:
    assert parse_side_info(b"\xd2\x00\x90\x01") == [(21.0, 40.0)]


def test_attach_side_info_tolerates_count_mismatch() -> None:
    points = list(make_points())
    merged = attach_side_info(points, [(30.0, 40.0)])
    assert merged[0].snr_db == pytest.approx(30.0)
    assert merged[1].snr_db == pytest.approx(18.2)


def test_assembler_handles_split_chunks() -> None:
    packet = encode_packet(frame_number=3, points=make_points())
    assembler = FrameAssembler()
    assert assembler.feed(packet[:11]) == []
    frames = assembler.feed(packet[11:])
    assert len(frames) == 1
    assert frames[0].header.frame_number == 3


def test_assembler_recovers_after_leading_garbage() -> None:
    packet = encode_packet(frame_number=9, points=make_points())
    frames = FrameAssembler().feed(b"\xde\xad\xbe\xef" + packet)
    assert len(frames) == 1
    assert frames[0].header.frame_number == 9


def test_assembler_yields_multiple_frames_from_one_chunk() -> None:
    blob = encode_packet(1, make_points()) + encode_packet(2, make_points())
    frames = FrameAssembler().feed(blob)
    assert [f.header.frame_number for f in frames] == [1, 2]


def test_assembler_rejects_absurd_total_length() -> None:
    packet = bytearray(encode_packet(1, make_points()))
    packet[12:16] = (999_999).to_bytes(4, "little")
    assembler = FrameAssembler()
    assert assembler.feed(bytes(packet)) == []
    assert assembler.dropped_frames >= 1


def test_assembler_drops_truncated_tlv_without_crashing() -> None:
    packet = encode_packet(1, make_points())
    truncated = bytearray(packet[: HEADER_LEN + 12])
    truncated[12:16] = len(truncated).to_bytes(4, "little")
    frames = FrameAssembler().feed(bytes(truncated))
    assert len(frames) == 1
    assert frames[0].points == ()
