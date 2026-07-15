"""Acquisition thread.

Serial reads must not run on the Qt event loop or the GUI stalls. This worker
owns the source and the pipeline and emits one report per frame.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

from .protocol import ProtocolError, SensorError

if TYPE_CHECKING:
    from .custom_types import DetectionReport, RadarFrame
    from .pipeline import DetectionPipeline
    from .sensor import FrameSource

logger: logging.Logger = logging.getLogger(__name__)


class AcquisitionWorker(QThread):
    """Pulls frames from a FrameSource, pushes DetectionReports to the GUI."""

    report_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished_cleanly = pyqtSignal()

    def __init__(self, source: FrameSource, pipeline: DetectionPipeline) -> None:
        super().__init__()
        self._source: FrameSource = source
        self._pipeline: DetectionPipeline = pipeline

    def run(self) -> None:
        """Thread entry point. All errors are reported, never swallowed."""
        try:
            self._pump()
        except (SensorError, ProtocolError) as exc:
            logger.error("Acquisition stopped: %s", exc)
            self.failed.emit(str(exc))
            return
        except OSError as exc:
            logger.error("Acquisition I/O error: %s", exc)
            self.failed.emit(f"I/O error: {exc}")
            return
        self.finished_cleanly.emit()

    def set_pipeline(self, pipeline: DetectionPipeline) -> None:
        """Swap in a retuned pipeline. Rebinding the reference is atomic, so the
        pump thread simply picks up the new object on its next frame.
        """
        self._pipeline = pipeline

    def _pump(self) -> None:
        for frame in self._source.frames():
            report: DetectionReport = self._pipeline.process(frame)
            self.report_ready.emit(report)
            if self.isInterruptionRequested():
                return

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Ask the source and thread to stop, then wait for the thread to exit."""
        self.requestInterruption()
        self._source.stop()
        if not self.wait(timeout_ms):
            logger.warning("Acquisition thread did not exit within %d ms", timeout_ms)
        self._source.close()

    def apply_frame(self, frame: RadarFrame) -> DetectionReport:
        """Process a single frame synchronously. Used by tests and replay."""
        return self._pipeline.process(frame)
