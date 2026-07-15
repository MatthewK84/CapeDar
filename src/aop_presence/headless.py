"""SSH-friendly text output for the detection pipeline."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import TYPE_CHECKING, TextIO

from .custom_types import DetectionReport, PresenceState
from .pipeline import DetectionPipeline

if TYPE_CHECKING:
    from .config import DetectionConfig
    from .sensor import FrameSource


class HeadlessMonitor:
    """Print state transitions immediately and a periodic stream heartbeat."""

    def __init__(self, status_interval_s: float = 1.0, stream: TextIO | None = None) -> None:
        if status_interval_s <= 0.0:
            raise ValueError("status_interval_s must be > 0")
        self._status_interval_s: float = status_interval_s
        self._stream: TextIO = stream or sys.stdout
        self._last_state: PresenceState = PresenceState.ABSENT
        self._last_status_s: float | None = None

    def update(self, report: DetectionReport) -> None:
        """Print any state transition followed by a rate-limited status line."""
        if report.state is not self._last_state:
            self._print_transition(report)
            self._last_state = report.state
        if self._status_due(report.host_timestamp_s):
            self._print_status(report)
            self._last_status_s = report.host_timestamp_s

    def _status_due(self, timestamp_s: float) -> bool:
        if self._last_status_s is None:
            return True
        return timestamp_s - self._last_status_s >= self._status_interval_s

    def _print_transition(self, report: DetectionReport) -> None:
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

    def _print_status(self, report: DetectionReport) -> None:
        self._write(
            f"STATUS frame={report.frame_number} state={report.state.value} "
            f"raw={report.raw_point_count} gated={len(report.gated_points)} "
            f"targets={len(report.targets)}"
        )

    def _write(self, message: str) -> None:
        timestamp: str = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"{timestamp} {message}", file=self._stream, flush=True)


def run_headless(
    source: FrameSource, config: DetectionConfig, status_interval_s: float = 1.0
) -> int:
    """Process frames synchronously until interrupted or the source stops."""
    pipeline: DetectionPipeline = DetectionPipeline(config)
    monitor: HeadlessMonitor = HeadlessMonitor(status_interval_s)
    sys.stdout.write("CapeDar headless monitor started; press Ctrl+C to stop.\n")
    sys.stdout.flush()
    try:
        for frame in source.frames():
            monitor.update(pipeline.process(frame))
    except KeyboardInterrupt:
        sys.stdout.write("\nCapeDar stopped.\n")
        sys.stdout.flush()
    return 0
