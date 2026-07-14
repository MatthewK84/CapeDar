"""Wire-level constants for the mmWave SDK Out-of-Box demo UART stream.

Reference: mmWave SDK 3.5/3.6 "Understanding UART Data Output Format".
Frame layout: 40-byte header, then ``num_tlvs`` blocks of (8-byte TL + payload).
The whole packet is zero-padded to a multiple of 32 bytes.
"""

from __future__ import annotations

from typing import Final

MAGIC_WORD: Final[bytes] = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
HEADER_STRUCT: Final[str] = "<8I"
HEADER_LEN: Final[int] = 40
TLV_HEADER_STRUCT: Final[str] = "<2I"
TLV_HEADER_LEN: Final[int] = 8
PACKET_PAD_MULTIPLE: Final[int] = 32

TLV_DETECTED_POINTS: Final[int] = 1
TLV_RANGE_PROFILE: Final[int] = 2
TLV_NOISE_PROFILE: Final[int] = 3
TLV_AZIMUTH_STATIC_HEATMAP: Final[int] = 4
TLV_RANGE_DOPPLER_HEATMAP: Final[int] = 5
TLV_STATS: Final[int] = 6
TLV_DETECTED_POINTS_SIDE_INFO: Final[int] = 7

POINT_STRUCT: Final[str] = "<4f"
POINT_LEN: Final[int] = 16
SIDE_INFO_STRUCT: Final[str] = "<2h"
SIDE_INFO_LEN: Final[int] = 4
SIDE_INFO_SCALE_DB: Final[float] = 0.1

PLATFORM_XWR6843: Final[int] = 0xA6843

CLI_BAUD: Final[int] = 115200
DATA_BAUD: Final[int] = 921600

MAX_PACKET_LEN: Final[int] = 65536
MAX_POINTS_PER_FRAME: Final[int] = 1000


class ProtocolError(Exception):
    """Base class for all UART framing and parsing failures."""


class FramingError(ProtocolError):
    """Raised when a byte stream does not contain a recoverable frame."""


class PayloadError(ProtocolError):
    """Raised when a TLV payload has an inconsistent length."""


class SensorError(Exception):
    """Base class for serial-port and sensor-configuration failures."""


class ConfigError(SensorError):
    """Raised when the radar rejects a CLI configuration command."""
