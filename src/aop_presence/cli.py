"""Command line entry point: ``capedar`` (alias ``aop-presence``).

Nothing is required. ``capedar`` with no arguments autodetects the EVM ports,
attaches to the sensor if it is already streaming, pushes the bundled profile
if it is not, opens the GPIO signal line if the platform has one, and prints to
stdout. Every flag below narrows that default, none of them enables it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .config import ConfigValidationError, DetectionConfig, load_detection_config
from .gpio import DEFAULT_SIGNAL_PIN, GpioError, GpioSettings, NullSink, create_signal_sink
from .protocol import ProtocolError, SensorError
from .sensor import RadarSensor, find_evm_ports
from .simulator import SCENARIO_PAIR, SCENARIO_SINGLE, SCENARIOS, SimulatedSensor

if TYPE_CHECKING:
    from .gpio import SignalSink
    from .sensor import FrameSource

logger: logging.Logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
CONFIGURE_MODES: Final[tuple[str, ...]] = ("auto", "always", "never")
GPIO_MODES: Final[tuple[str, ...]] = ("auto", "on", "off")


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="capedar",
        description=(
            "Presence, ranging, and multi-object signalling for the TI AWR6843AOPEVM. "
            "Runs with no arguments."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_source_args(parser)
    _add_detection_args(parser)
    _add_interface_args(parser)
    _add_gpio_args(parser)
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    return parser


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("sensor")
    group.add_argument("--cli-port", help="CLI serial port (default: autodetect)")
    group.add_argument("--data-port", help="Data serial port (default: autodetect)")
    group.add_argument(
        "--radar-cfg",
        type=Path,
        default=None,
        metavar="PATH",
        help="TI .cfg profile (default: the profile bundled in the package)",
    )
    group.add_argument(
        "--configure",
        choices=CONFIGURE_MODES,
        default="auto",
        help="auto attaches to an already-streaming sensor and only configures a quiet one",
    )
    group.add_argument(
        "--no-configure",
        action="store_true",
        help="Deprecated alias for --configure never",
    )
    group.add_argument("--simulate", action="store_true", help="Run with no hardware attached")
    group.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default=SCENARIO_SINGLE,
        help=f"Simulated scene; {SCENARIO_PAIR} exercises the multi-object signal",
    )


def _add_detection_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("detection")
    group.add_argument(
        "--detection-cfg",
        type=Path,
        default=None,
        metavar="PATH",
        help="JSON gate overrides (default: built-in gates, no file needed)",
    )
    group.add_argument(
        "--min-separation",
        type=float,
        default=None,
        metavar="METRES",
        help="Objects closer than this count as one; raise it if one person reads as two",
    )


def _add_interface_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("interface")
    group.add_argument(
        "--gui", action="store_true", help="Open the Qt window instead of printing to stdout"
    )
    group.add_argument(
        "--headless",
        action="store_true",
        help="Deprecated; headless is the default and this flag is accepted for compatibility",
    )
    group.add_argument("--json", action="store_true", help="Emit one JSON record per frame")
    group.add_argument(
        "--status-interval", type=float, default=1.0, metavar="SECONDS", help="Heartbeat period"
    )
    group.add_argument(
        "--stale-timeout",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Force the signal line low after this long without a frame",
    )


def _add_gpio_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("signal line")
    group.add_argument(
        "--gpio",
        choices=GPIO_MODES,
        default="auto",
        help="auto uses the pin when available, on requires it, off never touches hardware",
    )
    group.add_argument(
        "--gpio-pin",
        default=DEFAULT_SIGNAL_PIN,
        metavar="PIN",
        help="gpiozero pin spec; BOARD11 is physical pin 11 (BCM GPIO17)",
    )
    group.add_argument(
        "--gpio-active-low",
        action="store_true",
        help="Invert the line for modules that trigger on a low input",
    )


def resolve_detection_config(args: argparse.Namespace) -> DetectionConfig:
    """Defaults are usable as-is; a file or flag only ever narrows them."""
    base: DetectionConfig = (
        DetectionConfig()
        if args.detection_cfg is None
        else load_detection_config(args.detection_cfg)
    )
    if args.min_separation is None:
        return base
    return base.with_overrides(min_target_separation_m=args.min_separation)


def resolve_configure_mode(args: argparse.Namespace) -> str:
    if args.no_configure:
        logger.warning("--no-configure is deprecated; use --configure never")
        return "never"
    mode: str = args.configure
    return mode


def apply_profile(sensor: RadarSensor, args: argparse.Namespace) -> None:
    """Push a profile only if this sensor actually needs one."""
    mode: str = resolve_configure_mode(args)
    if mode == "never":
        logger.info("Skipping configuration at user request")
        return
    if mode == "auto" and sensor.is_streaming():
        logger.info("Sensor is already streaming; attaching without reconfiguring")
        return
    if args.radar_cfg is not None:
        sensor.configure(args.radar_cfg)
        return
    sensor.configure_default()


def open_hardware_source(args: argparse.Namespace) -> RadarSensor:
    """Open the EVM, autodetecting ports unless told otherwise."""
    cli_port: str = args.cli_port or ""
    data_port: str = args.data_port or ""
    if not cli_port or not data_port:
        cli_port, data_port = find_evm_ports()
        logger.info("Autodetected CLI=%s data=%s", cli_port, data_port)
    sensor: RadarSensor = RadarSensor(cli_port, data_port)
    sensor.open()
    apply_profile(sensor, args)
    return sensor


def build_source(args: argparse.Namespace) -> FrameSource:
    if args.simulate:
        logger.info("Simulation mode (%s); no hardware will be opened", args.scenario)
        return SimulatedSensor(scenario=args.scenario)
    return open_hardware_source(args)


def build_sink(args: argparse.Namespace) -> SignalSink:
    """Build the signal line, or a no-op stand-in when there is no GPIO."""
    if args.gui:
        return NullSink()
    settings: GpioSettings = GpioSettings(pin=args.gpio_pin, active_high=not args.gpio_active_low)
    sink: SignalSink = create_signal_sink(args.gpio, settings)
    if isinstance(sink, NullSink):
        logger.info("No signal line; multi-object events will be printed only")
    else:
        logger.info("Signal line on %s asserts while >1 object is confirmed", args.gpio_pin)
    return sink


def run_gui(source: FrameSource, config: DetectionConfig, title: str) -> int:
    """Start Qt. Imported late so headless use needs no display or PyQt6."""
    from PyQt6.QtWidgets import QApplication

    from .gui import MainWindow

    app: QApplication = QApplication(sys.argv)
    window: MainWindow = MainWindow(source, config, title)
    window.show()
    return int(app.exec())


def run_selected_interface(
    source: FrameSource, config: DetectionConfig, args: argparse.Namespace
) -> int:
    if args.gui:
        title: str = "AWR6843AOP Presence (SIMULATED)" if args.simulate else "AWR6843AOP Presence"
        return run_gui(source, config, title)
    from .headless import run_headless

    return run_headless(
        source,
        config,
        status_interval_s=args.status_interval,
        sink=build_sink(args),
        stale_timeout_s=args.stale_timeout,
        as_json=args.json,
    )


def validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.status_interval <= 0.0:
        parser.error("--status-interval must be greater than zero")
    if args.stale_timeout <= 0.0:
        parser.error("--stale-timeout must be greater than zero")
    if args.gui and args.json:
        parser.error("--json has no meaning with --gui")


def main(argv: list[str] | None = None) -> int:
    """Parse args, open a source, and run the selected interface."""
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args(argv)
    validate(parser, args)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config: DetectionConfig = resolve_detection_config(args)
        source: FrameSource = build_source(args)
    except (SensorError, ConfigValidationError, GpioError) as exc:
        logger.error("Startup failed: %s", exc)
        return EXIT_ERROR
    try:
        return run_selected_interface(source, config, args)
    except (SensorError, ProtocolError, GpioError) as exc:
        logger.error("Monitoring stopped: %s", exc)
        return EXIT_ERROR
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
