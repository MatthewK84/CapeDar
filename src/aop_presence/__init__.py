"""Presence detection, ranging, sizing, and multi-object signalling for the AWR6843AOPEVM.

Public entry points:
    DetectionPipeline -- RadarFrame -> DetectionReport
    RadarSensor       -- serial link to the EVM
    SimulatedSensor   -- hardware-free frame source
    create_signal_sink -- GPIO line that asserts while >1 object is confirmed
"""

from __future__ import annotations

from .config import DetectionConfig, load_detection_config
from .custom_types import (
    DetectedPoint,
    DetectionReport,
    FrameHeader,
    OccupancyState,
    PresenceState,
    RadarFrame,
    TargetCluster,
    TargetSize,
)
from .gpio import GpioError, GpioSettings, GpioSink, NullSink, SignalSink, create_signal_sink
from .multitarget import OccupancyTracker, resolve_distinct
from .parser import FrameAssembler, parse_packet
from .pipeline import DetectionPipeline
from .presence import Hysteresis, PresenceTracker
from .protocol import ConfigError, ProtocolError, SensorError
from .reader import FrameReader
from .sensor import FrameSource, RadarSensor, find_evm_ports
from .simulator import SimulatedSensor, encode_packet, make_frame

__version__: str = "0.1.0"

__all__ = [
    "ConfigError",
    "DetectedPoint",
    "DetectionConfig",
    "DetectionPipeline",
    "DetectionReport",
    "FrameAssembler",
    "FrameHeader",
    "FrameReader",
    "FrameSource",
    "GpioError",
    "GpioSettings",
    "GpioSink",
    "Hysteresis",
    "NullSink",
    "OccupancyState",
    "OccupancyTracker",
    "PresenceState",
    "PresenceTracker",
    "ProtocolError",
    "RadarFrame",
    "RadarSensor",
    "SensorError",
    "SignalSink",
    "SimulatedSensor",
    "TargetCluster",
    "TargetSize",
    "__version__",
    "create_signal_sink",
    "encode_packet",
    "find_evm_ports",
    "load_detection_config",
    "make_frame",
    "parse_packet",
    "resolve_distinct",
]
