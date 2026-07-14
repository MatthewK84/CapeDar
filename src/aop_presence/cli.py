"""Command line entry point: ``aop-presence``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Final

from .config import ConfigValidationError, DetectionConfig, load_detection_config
from .protocol import SensorError
from .sensor import FrameSource, RadarSensor, find_evm_ports
from .simulator import SimulatedSensor

logger: logging.Logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
DEFAULT_CFG: Final[Path] = Path("configs/aop_presence_10fps.cfg")


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="aop-presence",
        description="Presence and range GUI for the TI AWR6843AOPEVM.",
    )
    parser.add_argument("--cli-port", help="CLI serial port (default: autodetect)")
    parser.add_argument("--data-port", help="Data serial port (default: autodetect)")
    parser.add_argument("--radar-cfg", type=Path, default=DEFAULT_CFG, help="TI .cfg profile")
    parser.add_argument("--detection-cfg", type=Path, help="JSON detection gate overrides")
    parser.add_argument("--simulate", action="store_true", help="Run with no hardware attached")
    parser.add_argument("--no-configure", action="store_true", help="Skip pushing the .cfg")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    return parser


def resolve_detection_config(path: Path | None) -> DetectionConfig:
    if path is None:
        return DetectionConfig()
    return load_detection_config(path)


def open_hardware_source(args: argparse.Namespace) -> RadarSensor:
    """Open the EVM and push the profile unless told not to."""
    cli_port: str = args.cli_port or ""
    data_port: str = args.data_port or ""
    if not cli_port or not data_port:
        cli_port, data_port = find_evm_ports()
        logger.info("Autodetected CLI=%s data=%s", cli_port, data_port)
    sensor: RadarSensor = RadarSensor(cli_port, data_port)
    sensor.open()
    if not args.no_configure:
        sensor.configure(args.radar_cfg)
    return sensor


def build_source(args: argparse.Namespace) -> FrameSource:
    if args.simulate:
        logger.info("Running in simulation mode; no hardware will be opened")
        return SimulatedSensor()
    return open_hardware_source(args)


def run_gui(source: FrameSource, config: DetectionConfig, title: str) -> int:
    """Start Qt. Imported late so headless CLI use does not require a display."""
    from PyQt6.QtWidgets import QApplication

    from .gui import MainWindow

    app: QApplication = QApplication(sys.argv)
    window: MainWindow = MainWindow(source, config, title)
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    """Parse args, open a source, run the GUI. Returns a process exit code."""
    args: argparse.Namespace = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config: DetectionConfig = resolve_detection_config(args.detection_cfg)
        source: FrameSource = build_source(args)
    except (SensorError, ConfigValidationError) as exc:
        logger.error("Startup failed: %s", exc)
        return EXIT_ERROR
    title: str = "AWR6843AOP Presence (SIMULATED)" if args.simulate else "AWR6843AOP Presence"
    try:
        return run_gui(source, config, title)
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
