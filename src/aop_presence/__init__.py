"""Presence detection, ranging, and sizing for the TI AWR6843AOPEVM.

Public entry points:
    DetectionPipeline -- RadarFrame -> DetectionReport
    RadarSensor       -- serial link to the EVM
    SimulatedSensor   -- hardware-free frame source
"""

from __future__ import annotations

from .config import DetectionConfig, load_detection_config
from .parser import FrameAssembler, parse_packet
from .pipeline import DetectionPipeline
from .presence import PresenceTracker
from .protocol import ConfigError, ProtocolError, SensorError
from .sensor import FrameSource, RadarSensor, find_evm_ports
from .simulator import SimulatedSensor, encode_packet, make_frame
from .custom_types import (
    DetectedPoint,
    DetectionReport,
    FrameHeader,
    PresenceState,
    RadarFrame,
    TargetCluster,
    TargetSize,
)

__version__: str = "0.1.0"

__all__ = [
    "ConfigError",
    "DetectedPoint",
    "DetectionConfig",
    "DetectionPipeline",
    "DetectionReport",
    "FrameAssembler",
    "FrameHeader",
    "FrameSource",
    "PresenceState",
    "PresenceTracker",
    "ProtocolError",
    "RadarFrame",
    "RadarSensor",
    "SensorError",
    "SimulatedSensor",
    "TargetCluster",
    "TargetSize",
    "__version__",
    "encode_packet",
    "find_evm_ports",
    "load_detection_config",
    "make_frame",
    "parse_packet",
]
