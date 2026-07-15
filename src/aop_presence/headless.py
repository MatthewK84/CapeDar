"""SSH-friendly text output and signal-line driving for the detection pipeline.

No Qt, no display, no controlling terminal required. Runs under Windows
PowerShell, over SSH, or from a systemd unit on the Pi.

The signal line is asserted while occupancy is MULTIPLE, meaning more than one
resolvably distinct object is confirmed in front of the sensor. It is
de-asserted on every exit path, including radar silence, so the line never
outlives the evidence for it.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, TextIO

from .custom_types import DetectionReport, OccupancyState, PresenceState
from .gpio import NullSink
from .pipeline import DetectionPipeline
from .reader import FrameReader

if TYPE_CHECKING:
    from types import FrameType

    from .config import DetectionConfig
    from .custom_types import RadarFrame
    from .gpio import SignalSink
    from .sensor import FrameSource

logger: logging.Logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
POLL_TIMEOUT_S: Final[float] = 0.25
DEFAULT_STALE_TIMEOUT_S: Final[float] = 2.0


@dataclass(frozen=True, slots=True)
class HeadlessOptions:
    """Everything the runner needs that is not detection policy."""

    status_interval_s: float = 1.0
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S
    as_json: bool = False

    def __post_init__(self) -> None:
        if self.status_interval_s <= 0.0:
            raise ValueError("status_interval_s must be > 0")
        if self.stale_timeout_s <= 0.0:
            raise ValueError("stale_timeout_s must be > 0")


def report_to_dict(report: DetectionReport) -> dict[str, Any]:
    """Flatten a report into one JSON-serialisable record."""
    target = report.primary
    record: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "frame": report.frame_number,
        "state": report.state.value,
        "occupancy": report.occupancy.value,
        "multi_target": report.multi_target,
        "distinct_count": report.distinct_count,
        "raw_points": report.raw_point_count,
        "gated_points": len(report.gated_points),
    }
    if target is not None:
        record["primary"] = {
            "range_m": round(target.range_m, 3),
            "azimuth_deg": round(target.azimuth_deg, 2),
            "velocity_mps": round(target.radial_velocity_mps, 3),
            "snr_db": round(target.peak_snr_db, 1),
            "points": target.point_count,
        }
    return record


class StopRequest:
    """Latches a request to shut down cleanly from SIGINT or SIGTERM.

    systemd sends SIGTERM. Without a handler the process dies mid-loop and the
    GPIO line is released by the kernel in an undefined order. Latching a flag
    and unwinding normally guarantees the de-assert runs first.
    """

    def __init__(self) -> None:
        self._requested: bool = False

    @property
    def requested(self) -> bool:
        return self._requested

    def request(self) -> None:
        self._requested = True

    def handle(self, signum: int, frame: FrameType | None) -> None:
        """Signal handler. Stays trivial; it runs between bytecodes."""
        del frame
        logger.info("Received signal %d, shutting down", signum)
        self._requested = True


def install_handlers(stop: StopRequest) -> None:
    """Register SIGINT/SIGTERM handlers where the platform supports them."""
    for name in ("SIGINT", "SIGTERM"):
        sig: signal.Signals | None = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, stop.handle)
        except (ValueError, OSError) as exc:
            logger.debug("Cannot install %s handler: %s", name, exc)


class HeadlessMonitor:
    """Prints transitions immediately and a rate-limited status heartbeat."""

    def __init__(self, options: HeadlessOptions, stream: TextIO | None = None) -> None:
        self._options: HeadlessOptions = options
        self._stream: TextIO = stream or sys.stdout
        self._last_state: PresenceState = PresenceState.ABSENT
        self._last_occupancy: OccupancyState = OccupancyState.EMPTY
        self._last_status_s: float | None = None

    def update(self, report: DetectionReport) -> None:
        """Emit whatever this frame warrants."""
        if self._options.as_json:
            self._write_line(json.dumps(report_to_dict(report)))
            return
        if report.state is not self._last_state:
            self._print_presence(report)
        if report.occupancy is not self._last_occupancy:
            self._print_occupancy(report)
        self._last_state = report.state
        self._last_occupancy = report.occupancy
        if self._status_due(report.host_timestamp_s):
            self._print_status(report)
            self._last_status_s = report.host_timestamp_s

    def note_stale(self) -> None:
        """Report that the stream went quiet and the signal was forced low."""
        self._last_state = PresenceState.ABSENT
        self._last_occupancy = OccupancyState.EMPTY
        if self._options.as_json:
            self._write_line(json.dumps({"event": "STALE", "multi_target": False}))
            return
        self._write("STALE no frames within timeout; signal forced LOW")

    def _status_due(self, timestamp_s: float) -> bool:
        if self._last_status_s is None:
            return True
        return timestamp_s - self._last_status_s >= self._options.status_interval_s

    def _print_presence(self, report: DetectionReport) -> None:
        target = report.primary
        if report.state is PresenceState.PRESENT and target is not None:
            details: str = (
                f"range={target.range_m:.2f}m azimuth={target.azimuth_deg:+.1f}deg "
                f"velocity={target.radial_velocity_mps:+.2f}m/s "
                f"snr={target.peak_snr_db:.1f}dB points={target.point_count}"
            )
            self._write(f"DETECTED frame={report.frame_number} {details}")
            return
        self._write(f"CLEARED frame={report.frame_number}")

    def _print_occupancy(self, report: DetectionReport) -> None:
        if report.occupancy is OccupancyState.MULTIPLE:
            ranges: str = ",".join(f"{t.range_m:.2f}m" for t in report.distinct_targets)
            self._write(
                f"MULTI frame={report.frame_number} objects={report.distinct_count} "
                f"ranges={ranges} signal=HIGH"
            )
            return
        if self._last_occupancy is OccupancyState.MULTIPLE:
            self._write(
                f"MULTI-CLEARED frame={report.frame_number} "
                f"objects={report.distinct_count} signal=LOW"
            )

    def _print_status(self, report: DetectionReport) -> None:
        self._write(
            f"STATUS frame={report.frame_number} state={report.state.value} "
            f"occupancy={report.occupancy.value} raw={report.raw_point_count} "
            f"gated={len(report.gated_points)} distinct={report.distinct_count}"
        )

    def _write(self, message: str) -> None:
        timestamp: str = datetime.now().astimezone().isoformat(timespec="seconds")
        self._write_line(f"{timestamp} {message}")

    def _write_line(self, line: str) -> None:
        self._stream.write(f"{line}\n")
        self._stream.flush()


class HeadlessRunner:
    """Owns the frame loop, the signal line, and the fail-safe watchdog."""

    def __init__(
        self,
        source: FrameSource,
        config: DetectionConfig,
        options: HeadlessOptions | None = None,
        sink: SignalSink | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self._options: HeadlessOptions = options or HeadlessOptions()
        self._pipeline: DetectionPipeline = DetectionPipeline(config)
        self._reader: FrameReader = FrameReader(source)
        self._sink: SignalSink = sink if sink is not None else NullSink()
        self._monitor: HeadlessMonitor = HeadlessMonitor(self._options, stream)
        self._stop: StopRequest = StopRequest()
        self._last_frame_s: float | None = None

    def request_stop(self) -> None:
        """Ask the loop to finish after the current frame."""
        self._stop.request()

    def run(self) -> int:
        """Process frames until interrupted, the source ends, or it errors."""
        install_handlers(self._stop)
        self._reader.start()
        try:
            self._loop()
        finally:
            self._shutdown()
        return self._exit_code()

    def _loop(self) -> None:
        while not self._stop.requested:
            frame: RadarFrame | None = self._reader.next_frame(POLL_TIMEOUT_S)
            now: float = time.monotonic()
            if frame is None:
                if self._reader.finished:
                    return
                self._check_stale(now)
                continue
            self._last_frame_s = now
            self._handle(frame)

    def _handle(self, frame: RadarFrame) -> None:
        report: DetectionReport = self._pipeline.process(frame)
        self._sink.set_state(report.multi_target)
        self._monitor.update(report)

    def _check_stale(self, now: float) -> None:
        """Force the line low when the radar has gone quiet for too long."""
        if self._last_frame_s is None:
            return
        if now - self._last_frame_s < self._options.stale_timeout_s:
            return
        self._pipeline.reset()
        self._sink.set_state(False)
        self._monitor.note_stale()
        self._last_frame_s = None

    def _shutdown(self) -> None:
        """De-assert first, then release everything. Never raises."""
        try:
            self._sink.set_state(False)
        except Exception as exc:
            logger.warning("Could not de-assert signal line: %s", exc)
        self._sink.close()
        self._reader.stop()
        if self._reader.dropped_frames:
            logger.warning("Dropped %d frames; consumer fell behind", self._reader.dropped_frames)

    def _exit_code(self) -> int:
        error: BaseException | None = self._reader.error
        if error is None or isinstance(error, KeyboardInterrupt):
            return EXIT_OK
        logger.error("Acquisition failed: %s", error)
        return EXIT_ERROR


def run_headless(
    source: FrameSource,
    config: DetectionConfig,
    status_interval_s: float = 1.0,
    sink: SignalSink | None = None,
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
    as_json: bool = False,
) -> int:
    """Process frames headlessly until interrupted. Returns a process exit code."""
    options: HeadlessOptions = HeadlessOptions(
        status_interval_s=status_interval_s,
        stale_timeout_s=stale_timeout_s,
        as_json=as_json,
    )
    runner: HeadlessRunner = HeadlessRunner(source, config, options, sink)
    if not as_json:
        sys.stdout.write("CapeDar headless monitor started; press Ctrl+C to stop.\n")
        sys.stdout.flush()
    return runner.run()
