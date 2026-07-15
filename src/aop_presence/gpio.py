"""Drive a physical signal line when more than one object is confirmed.

Wiring, Raspberry Pi 5 40-pin header, physical (BOARD) numbering:

    Pin 1  -- 3V3 power  -> module VCC
    Pin 9  -- Ground     -> module GND
    Pin 11 -- GPIO17     -> module signal input

Pin 11 is BCM GPIO17. Physical numbering is used throughout this module
because that is what you count on the header with your thumb.

Raspberry Pi 5 note
    The Pi 5 moves its GPIO behind the RP1 southbridge. RPi.GPIO does not work
    on it, and pigpio does not support it, so remote-GPIO over the network is
    not an option either. gpiozero backed by lgpio is used instead, which is
    the supported Pi 5 path. gpiozero also resolves the gpiochip number
    itself, which matters because that number moved between Pi OS releases.

Fail-safe behaviour
    The line is driven low on open, on close, and on any error. A latched-high
    line after the radar dies would be a lie about the world, so every exit
    path de-asserts.

Current limit
    GPIO17 sources 16 mA maximum. Drive an opto-isolated or transistor module,
    not a relay coil or a bare LED without a series resistor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_SIGNAL_PIN: Final[str] = "BOARD11"


class GpioError(RuntimeError):
    """Raised when a GPIO line was explicitly requested but cannot be driven."""


@dataclass(frozen=True, slots=True)
class GpioSettings:
    """How to drive the signal line."""

    pin: str = DEFAULT_SIGNAL_PIN
    active_high: bool = True

    def __post_init__(self) -> None:
        if not self.pin:
            raise GpioError("pin must be a non-empty gpiozero pin spec, e.g. BOARD11")


@runtime_checkable
class SignalSink(Protocol):
    """Anything that can represent a two-state signal."""

    def set_state(self, asserted: bool) -> None:
        """Assert or de-assert the signal. Must be idempotent."""
        ...

    def close(self) -> None:
        """Release the line, leaving it de-asserted. Must never raise."""
        ...


class NullSink:
    """No hardware. Used on Windows, in tests, and when GPIO is turned off."""

    def __init__(self) -> None:
        self._asserted: bool = False

    @property
    def asserted(self) -> bool:
        return self._asserted

    def set_state(self, asserted: bool) -> None:
        self._asserted = asserted

    def close(self) -> None:
        self._asserted = False


class GpioSink:
    """Drives one real output pin through gpiozero.

    The gpiozero import is deliberately local to ``__init__`` so that importing
    this module costs nothing on a machine with no GPIO, which is what lets the
    same code run under Windows PowerShell.
    """

    def __init__(self, settings: GpioSettings | None = None) -> None:
        self._settings: GpioSettings = settings or GpioSettings()
        self._device: Any = self._open_device()
        self._asserted: bool = False

    def _open_device(self) -> Any:
        try:
            from gpiozero import DigitalOutputDevice
        except ImportError as exc:
            raise GpioError(
                "gpiozero is not installed. On a Raspberry Pi 5 run "
                'pip install "aop-presence[pi]". Use --gpio off elsewhere.'
            ) from exc
        try:
            return DigitalOutputDevice(
                self._settings.pin,
                active_high=self._settings.active_high,
                initial_value=False,
            )
        except Exception as exc:
            raise GpioError(f"Cannot open GPIO {self._settings.pin}: {exc}") from exc

    @property
    def pin(self) -> str:
        return self._settings.pin

    @property
    def asserted(self) -> bool:
        return self._asserted

    def __enter__(self) -> GpioSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def set_state(self, asserted: bool) -> None:
        """Drive the line. Repeated calls with the same value do no I/O."""
        if asserted == self._asserted:
            return
        try:
            self._device.on() if asserted else self._device.off()
        except Exception as exc:
            raise GpioError(f"Cannot write GPIO {self._settings.pin}: {exc}") from exc
        self._asserted = asserted
        logger.debug("GPIO %s -> %s", self._settings.pin, "HIGH" if asserted else "LOW")

    def close(self) -> None:
        """De-assert and release the line. Never raises."""
        try:
            self._device.off()
            self._device.close()
        except Exception as exc:
            logger.warning("Error releasing GPIO %s: %s", self._settings.pin, exc)
        self._asserted = False


def create_signal_sink(mode: str, settings: GpioSettings | None = None) -> SignalSink:
    """Build a sink for the requested mode.

    off
        Never touch hardware.
    on
        Open the pin or fail loudly. Use this on the Pi, where a silently
        dead signal line is worse than a refusal to start.
    auto
        Open the pin if possible, otherwise warn and continue without it. This
        is what lets one command line work on both a Pi and a Windows laptop.
    """
    if mode == "off":
        return NullSink()
    if mode == "on":
        return GpioSink(settings)
    if mode != "auto":
        raise GpioError(f"Unknown gpio mode {mode!r}; expected auto, on, or off")
    try:
        return GpioSink(settings)
    except GpioError as exc:
        logger.warning("GPIO unavailable, continuing without a signal line: %s", exc)
        return NullSink()
