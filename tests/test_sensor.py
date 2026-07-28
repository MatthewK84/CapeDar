"""Serial layer behaviour, exercised against a fake port instead of an EVM."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import serial

from aop_presence.protocol import ConfigError, SensorError
from aop_presence.sensor import RadarSensor, find_evm_ports, read_config_lines
from aop_presence.simulator import encode_packet

if TYPE_CHECKING:
    from aop_presence.custom_types import DetectedPoint

PROMPT = b"mmwDemo:/>"


class FakePort:
    """Stands in for serial.Serial. Records writes, replays canned reads."""

    def __init__(self, reply: bytes = PROMPT, stream: bytes = b"") -> None:
        self.is_open = True
        self.written: list[bytes] = []
        self.reply = reply
        self._stream = stream
        self.closed = False

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, terminator: bytes) -> bytes:
        del terminator
        return self.reply

    @property
    def in_waiting(self) -> int:
        return len(self._stream)

    def read(self, size: int) -> bytes:
        chunk, self._stream = self._stream[:size], self._stream[size:]
        return chunk

    def close(self) -> None:
        self.closed = True
        self.is_open = False


class FakeComPort:
    def __init__(self, device: str, vid: int) -> None:
        self.device = device
        self.vid = vid


def attach(sensor: RadarSensor, cli: FakePort | None, data: FakePort | None) -> None:
    """Inject fake ports without opening a real one."""
    sensor._cli = cli
    sensor._data = data


# --- port discovery ---------------------------------------------------------


def test_finds_the_cp2105_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    ports = [FakeComPort("/dev/ttyUSB1", 0x10C4), FakeComPort("/dev/ttyUSB0", 0x10C4)]
    monkeypatch.setattr("aop_presence.sensor.list_ports.comports", lambda: ports)
    assert find_evm_ports() == ("/dev/ttyUSB0", "/dev/ttyUSB1")


def test_ignores_non_evm_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    ports = [FakeComPort("/dev/ttyUSB0", 0x10C4), FakeComPort("/dev/ttyACM0", 0x2341)]
    monkeypatch.setattr("aop_presence.sensor.list_ports.comports", lambda: ports)
    with pytest.raises(SensorError, match="found 1"):
        find_evm_ports()


def test_no_ports_names_the_override_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aop_presence.sensor.list_ports.comports", list)
    with pytest.raises(SensorError, match="--cli-port"):
        find_evm_ports()


# --- opening ----------------------------------------------------------------


def test_open_failure_raises_and_leaves_nothing_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> FakePort:
        raise serial.SerialException("port busy")

    monkeypatch.setattr("aop_presence.sensor.serial.Serial", explode)
    device = RadarSensor("/dev/ttyUSB0", "/dev/ttyUSB1")
    with pytest.raises(SensorError, match="Cannot open EVM ports"):
        device.open()


def test_close_is_safe_on_an_unopened_sensor() -> None:
    RadarSensor("/dev/ttyUSB0", "/dev/ttyUSB1").close()


def test_close_releases_both_ports() -> None:
    device = RadarSensor("a", "b")
    cli, data = FakePort(), FakePort()
    attach(device, cli, data)
    device.close()
    assert cli.closed
    assert data.closed


def test_close_survives_a_port_that_throws() -> None:
    class AngryPort(FakePort):
        def close(self) -> None:
            raise serial.SerialException("already gone")

    device = RadarSensor("a", "b")
    attach(device, AngryPort(), FakePort())
    device.close()


# --- CLI commands -----------------------------------------------------------


def test_command_is_newline_terminated() -> None:
    device = RadarSensor("a", "b")
    cli = FakePort()
    attach(device, cli, None)
    device.send_command("sensorStart")
    assert cli.written == [b"sensorStart\n"]


def test_command_without_an_open_port_raises() -> None:
    with pytest.raises(SensorError, match="CLI port is not open"):
        RadarSensor("a", "b").send_command("sensorStart")


def test_radar_rejection_becomes_a_config_error() -> None:
    device = RadarSensor("a", "b")
    attach(device, FakePort(reply=b"Error: invalid command\nmmwDemo:/>"), None)
    with pytest.raises(ConfigError, match="Radar rejected"):
        device.send_command("nonsenseCmd")


def test_apply_commands_sends_stop_first() -> None:
    """A sensorStop before reconfiguring is what makes re-config work."""
    device = RadarSensor("a", "b")
    cli = FakePort()
    attach(device, cli, None)
    device.apply_commands(["dfeDataOutputMode 1", "sensorStart"])
    assert cli.written[0] == b"sensorStop\n"
    assert cli.written[-1] == b"sensorStart\n"


def test_apply_commands_rejects_an_empty_profile() -> None:
    device = RadarSensor("a", "b")
    attach(device, FakePort(), None)
    with pytest.raises(ConfigError, match="No radar config commands"):
        device.apply_commands([])


def test_stop_on_an_already_stopped_sensor_is_tolerated() -> None:
    """The EVM errors on a redundant sensorStop. That is expected, not fatal."""
    device = RadarSensor("a", "b")
    attach(device, FakePort(reply=b"Error: not started\nmmwDemo:/>"), None)
    with pytest.raises(ConfigError):
        device.apply_commands(["dfeDataOutputMode 1"])


def test_bundled_profile_reaches_the_wire() -> None:
    device = RadarSensor("a", "b")
    cli = FakePort()
    attach(device, cli, None)
    device.configure_default()
    assert len(cli.written) > 20
    assert cli.written[-1] == b"sensorStart\n"


def test_airborne_profile_reaches_the_wire() -> None:
    device = RadarSensor("a", "b")
    cli = FakePort()
    attach(device, cli, None)
    device.configure_default("airborne_5m.cfg")
    sent = b"".join(cli.written)
    assert b"profileCfg 0 60 10 6 21 0 0 98 1 64 5209 0 0 158\n" in sent


# --- config files -----------------------------------------------------------


def test_config_file_comments_and_blanks_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "p.cfg"
    path.write_text("% a comment\n\nsensorStop\n   \nsensorStart\n", encoding="utf-8")
    assert read_config_lines(path) == ["sensorStop", "sensorStart"]


@pytest.mark.parametrize(
    "name",
    [
        "airborne_5m.cfg",
        "aop_presence_10fps.cfg",
        "recommended_1.cfg",
        "recommended_2_no_noise.cfg",
    ],
)
def test_shipped_profiles_configure_both_cfar_fov_directions(name: str) -> None:
    """SDK 3.6 rejects a first sensorStart unless both FOV blocks were set."""
    path = Path(__file__).resolve().parents[1] / "configs" / name
    commands = read_config_lines(path)
    assert any(command.startswith("cfarFovCfg -1 0 ") for command in commands)
    assert any(command.startswith("cfarFovCfg -1 1 ") for command in commands)


def test_missing_config_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Cannot read radar config"):
        read_config_lines(tmp_path / "absent.cfg")


def test_configure_rejects_a_commentonly_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.cfg"
    path.write_text("% nothing here\n", encoding="utf-8")
    device = RadarSensor("a", "b")
    attach(device, FakePort(), None)
    with pytest.raises(ConfigError, match="no commands"):
        device.configure(path)


# --- attach-or-configure probe ----------------------------------------------


def points() -> tuple[DetectedPoint, ...]:
    from aop_presence.custom_types import DetectedPoint

    return (DetectedPoint(0.0, 2.0, 0.0, -0.3, 25.0),)


def test_streaming_sensor_is_detected() -> None:
    device = RadarSensor("a", "b")
    attach(device, None, FakePort(stream=encode_packet(1, points())))
    assert device.is_streaming(timeout_s=0.5)


def test_quiet_sensor_is_not_mistaken_for_streaming() -> None:
    device = RadarSensor("a", "b")
    attach(device, None, FakePort(stream=b""))
    assert not device.is_streaming(timeout_s=0.2)


def test_probe_without_a_data_port_raises() -> None:
    with pytest.raises(SensorError, match="Data port is not open"):
        RadarSensor("a", "b").is_streaming(timeout_s=0.1)


def test_frames_without_a_data_port_raises() -> None:
    with pytest.raises(SensorError, match="Data port is not open"):
        next(RadarSensor("a", "b").frames())


def test_frames_yields_parsed_packets() -> None:
    device = RadarSensor("a", "b")
    attach(device, None, FakePort(stream=encode_packet(7, points())))
    frame = next(iter(device.frames()))
    assert frame.header.frame_number == 7
    assert len(frame.points) == 1


def test_read_failure_surfaces_as_sensor_error() -> None:
    class DeadPort(FakePort):
        @property
        def in_waiting(self) -> int:
            raise serial.SerialException("device disconnected")

    device = RadarSensor("a", "b")
    attach(device, None, DeadPort())
    with pytest.raises(SensorError, match="Data port read failed"):
        next(iter(device.frames()))
