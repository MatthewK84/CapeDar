"""Serial link to the AWR6843AOPEVM.

The EVM enumerates two ports through its CP2105 bridge: an enhanced port for
CLI commands at 115200 and a standard port for the data stream at 921600.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import serial
from serial.tools import list_ports

from .parser import FrameAssembler
from .protocol import CLI_BAUD, DATA_BAUD, MAGIC_WORD, ConfigError, SensorError
from .resources import default_radar_commands, parse_config_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import TracebackType

    from .custom_types import RadarFrame

logger: logging.Logger = logging.getLogger(__name__)

CLI_PROMPT: Final[bytes] = b"mmwDemo:/>"
CLI_ERROR_TOKEN: Final[bytes] = b"Error"
CLI_COMMAND_DELAY_S: Final[float] = 0.03
CLI_RESPONSE_TIMEOUT_S: Final[float] = 1.0
READ_CHUNK_BYTES: Final[int] = 4096
DATA_READ_TIMEOUT_S: Final[float] = 0.5
EVM_VID_CP2105: Final[int] = 0x10C4
STREAM_PROBE_TIMEOUT_S: Final[float] = 1.5


@runtime_checkable
class FrameSource(Protocol):
    """Anything the GUI can pull frames from: real sensor or simulator."""

    def frames(self) -> Iterator[RadarFrame]:
        """Yield frames until stopped or the source is exhausted."""
        ...

    def stop(self) -> None:
        """Ask the source to end its frames() iterator."""
        ...

    def close(self) -> None:
        """Release underlying resources."""
        ...


def find_evm_ports() -> tuple[str, str]:
    """Return (cli_port, data_port) for the first CP2105 bridge found."""
    candidates: list[str] = [
        port.device for port in list_ports.comports() if port.vid == EVM_VID_CP2105
    ]
    if len(candidates) < 2:
        raise SensorError(
            f"Expected 2 CP2105 ports for the AWR6843AOPEVM, found {len(candidates)}. "
            "Pass --cli-port and --data-port explicitly."
        )
    candidates.sort()
    return candidates[0], candidates[1]


def read_config_lines(path: Path) -> list[str]:
    """Read a .cfg file, dropping comments and blank lines."""
    try:
        text: str = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read radar config {path}: {exc}") from exc
    return parse_config_text(text)


class RadarSensor:
    """Owns both serial ports and turns the data port into a frame iterator."""

    def __init__(
        self,
        cli_port: str,
        data_port: str,
        cli_baud: int = CLI_BAUD,
        data_baud: int = DATA_BAUD,
    ) -> None:
        self._cli_port: str = cli_port
        self._data_port: str = data_port
        self._cli_baud: int = cli_baud
        self._data_baud: int = data_baud
        self._cli: serial.Serial | None = None
        self._data: serial.Serial | None = None
        self._assembler: FrameAssembler = FrameAssembler()
        self._running: bool = False

    def __enter__(self) -> RadarSensor:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        """Open both ports. Safe to call once; raises SensorError on failure."""
        try:
            self._cli = serial.Serial(
                self._cli_port, self._cli_baud, timeout=CLI_RESPONSE_TIMEOUT_S
            )
            self._data = serial.Serial(
                self._data_port, self._data_baud, timeout=DATA_READ_TIMEOUT_S
            )
        except serial.SerialException as exc:
            self.close()
            raise SensorError(f"Cannot open EVM ports: {exc}") from exc
        logger.info("Opened CLI %s and data %s", self._cli_port, self._data_port)

    def close(self) -> None:
        """Close both ports. Never raises."""
        self._running = False
        for port in (self._cli, self._data):
            if port is None or not port.is_open:
                continue
            try:
                port.close()
            except serial.SerialException as exc:
                logger.warning("Error closing port: %s", exc)
        self._cli = None
        self._data = None

    def send_command(self, command: str) -> str:
        """Send one CLI line and return the radar's reply."""
        if self._cli is None:
            raise SensorError("CLI port is not open")
        try:
            self._cli.reset_input_buffer()
            self._cli.write(f"{command}\n".encode("ascii"))
            self._cli.flush()
            time.sleep(CLI_COMMAND_DELAY_S)
            reply: bytes = self._cli.read_until(CLI_PROMPT)
        except serial.SerialException as exc:
            raise SensorError(f"CLI write failed for {command!r}: {exc}") from exc
        if CLI_ERROR_TOKEN in reply:
            raise ConfigError(f"Radar rejected {command!r}: {reply.decode('ascii', 'replace')}")
        return reply.decode("ascii", "replace")

    def configure(self, config_path: Path) -> None:
        """Push a .cfg profile from disk."""
        commands: list[str] = read_config_lines(config_path)
        if not commands:
            raise ConfigError(f"Radar config {config_path} contains no commands")
        self.apply_commands(commands)
        logger.info("Applied %d config commands from %s", len(commands), config_path)

    def configure_default(self) -> None:
        """Push the profile bundled inside the package. Needs no files on disk."""
        commands: list[str] = default_radar_commands()
        self.apply_commands(commands)
        logger.info("Applied %d config commands from the bundled profile", len(commands))

    def apply_commands(self, commands: list[str]) -> None:
        """Push CLI commands line by line. sensorStop first, so re-config works."""
        if not commands:
            raise ConfigError("No radar config commands to apply")
        self._send_stop_quietly()
        for command in commands:
            self.send_command(command)

    def is_streaming(self, timeout_s: float = STREAM_PROBE_TIMEOUT_S) -> bool:
        """Listen for a magic word to see whether the sensor is already running.

        Lets the tool attach to a sensor someone else configured, or one left
        running by a previous run, instead of insisting on a fresh profile push.
        """
        if self._data is None:
            raise SensorError("Data port is not open")
        deadline: float = time.monotonic() + timeout_s
        tail: bytes = b""
        while time.monotonic() < deadline:
            chunk: bytes = self._read_chunk(self._data)
            if not chunk:
                continue
            buffered: bytes = tail + chunk
            if MAGIC_WORD in buffered:
                return True
            # Keep enough tail that a magic word split across reads still matches.
            tail = buffered[-(len(MAGIC_WORD) - 1) :]
        return False

    def _send_stop_quietly(self) -> None:
        """A sensorStop on an already-stopped sensor errors; that is expected."""
        try:
            self.send_command("sensorStop")
        except ConfigError:
            logger.debug("sensorStop rejected (sensor was already stopped)")

    def stop(self) -> None:
        self._running = False

    def frames(self) -> Iterator[RadarFrame]:
        """Yield parsed frames until stop() is called or the port dies."""
        if self._data is None:
            raise SensorError("Data port is not open")
        self._running = True
        while self._running:
            chunk: bytes = self._read_chunk(self._data)
            if not chunk:
                continue
            yield from self._assembler.feed(chunk)

    def _read_chunk(self, port: serial.Serial) -> bytes:
        try:
            waiting: int = port.in_waiting
            return bytes(port.read(max(waiting, 1)))
        except serial.SerialException as exc:
            self._running = False
            raise SensorError(f"Data port read failed: {exc}") from exc
