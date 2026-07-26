"""Presence detection, ranging, sizing, and multi-object signalling for the AWR6843AOPEVM.

Public entry points:
    DetectionPipeline -- RadarFrame -> DetectionReport
    RadarSensor       -- serial link to the EVM
    SimulatedSensor   -- hardware-free frame source
    create_signal_sink -- GPIO line that asserts while >1 object is confirmed
"""

from __future__ import annotations

from .airborne import apply_airborne_gates, estimate_forward_speed, height_above_ground_m
from .config import (
    AIRBORNE_5M,
    OUTDOOR_GROUND,
    DetectionConfig,
    load_detection_config,
    preset_config,
)
from .custom_types import (
    DetectedPoint,
    DetectionReport,
    EgoEstimate,
    FrameHeader,
    OccupancyState,
    PresenceState,
    RadarFrame,
    TargetCluster,
    TargetSize,
    TemperatureReport,
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

__version__: str = "0.2.0"

__all__ = [
    "AIRBORNE_5M",
    "OUTDOOR_GROUND",
    "ConfigError",
    "DetectedPoint",
    "DetectionConfig",
    "DetectionPipeline",
    "DetectionReport",
    "EgoEstimate",
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
    "TemperatureReport",
    "__version__",
    "apply_airborne_gates",
    "create_signal_sink",
    "encode_packet",
    "estimate_forward_speed",
    "find_evm_ports",
    "height_above_ground_m",
    "load_detection_config",
    "make_frame",
    "parse_packet",
    "preset_config",
    "resolve_distinct",
]
