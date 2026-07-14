"""Main window: presence banner, distance/size readouts, live gate controls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .pipeline import DetectionPipeline
from .plotview import RadarPlot
from .types import DetectionReport, PresenceState, TargetCluster
from .worker import AcquisitionWorker

if TYPE_CHECKING:
    from PyQt6.QtGui import QCloseEvent

    from .config import DetectionConfig
    from .sensor import FrameSource

logger: logging.Logger = logging.getLogger(__name__)

PRESENT_STYLE: Final[str] = (
    "background-color:#1f7a3f;color:white;font-size:26px;font-weight:bold;padding:14px;"
)
ABSENT_STYLE: Final[str] = (
    "background-color:#2b2b2b;color:#9a9a9a;font-size:26px;font-weight:bold;padding:14px;"
)
READOUT_STYLE: Final[str] = "font-size:34px;font-weight:bold;color:#e8e8e8;"
DASH: Final[str] = "--"


class ReadoutPanel(QWidget):
    """Numeric readouts for the closest confirmed target."""

    def __init__(self) -> None:
        super().__init__()
        self.distance: QLabel = QLabel(DASH)
        self.azimuth: QLabel = QLabel(DASH)
        self.extent: QLabel = QLabel(DASH)
        self.velocity: QLabel = QLabel(DASH)
        self.snr: QLabel = QLabel(DASH)
        self.points: QLabel = QLabel(DASH)
        self.caveat: QLabel = QLabel("")
        self._build()

    def _build(self) -> None:
        self.distance.setStyleSheet(READOUT_STYLE)
        self.caveat.setStyleSheet("color:#c8a02c;font-size:11px;")
        self.caveat.setWordWrap(True)
        form: QFormLayout = QFormLayout()
        form.addRow("Distance", self.distance)
        form.addRow("Azimuth", self.azimuth)
        form.addRow("Size (W x H)", self.extent)
        form.addRow("Radial velocity", self.velocity)
        form.addRow("Peak SNR", self.snr)
        form.addRow("Points in cluster", self.points)
        form.addRow(self.caveat)
        self.setLayout(form)

    def clear(self) -> None:
        for label in (self.distance, self.azimuth, self.extent, self.velocity, self.snr):
            label.setText(DASH)
        self.points.setText("0")
        self.caveat.setText("")

    def show_target(self, target: TargetCluster) -> None:
        self.distance.setText(f"{target.range_m:.2f} m")
        self.azimuth.setText(f"{target.azimuth_deg:+.1f} deg")
        self.extent.setText(f"{target.size.width_m:.2f} x {target.size.height_m:.2f} m")
        self.velocity.setText(f"{target.radial_velocity_mps:+.2f} m/s")
        self.snr.setText(f"{target.peak_snr_db:.1f} dB")
        self.points.setText(str(target.point_count))
        self.caveat.setText(
            f"Size is resolution-limited (cell {target.size.cross_range_cell_m:.2f} m "
            f"at this range); treat as an upper bound on precision."
            if target.size.resolution_limited
            else ""
        )


class ControlPanel(QGroupBox):
    """Live gate tuning. Emits nothing; the window polls current values."""

    def __init__(self, config: DetectionConfig) -> None:
        super().__init__("Detection gates")
        self.min_snr: QDoubleSpinBox = self._make_double(0.0, 60.0, 0.5, config.min_snr_db, " dB")
        self.max_range: QDoubleSpinBox = self._make_double(
            0.5, 20.0, 0.5, config.max_range_m, " m"
        )
        self.max_azimuth: QDoubleSpinBox = self._make_double(
            5.0, 90.0, 5.0, config.max_azimuth_deg, " deg"
        )
        self.cluster_eps: QDoubleSpinBox = self._make_double(
            0.05, 2.0, 0.05, config.cluster_eps_m, " m"
        )
        self.min_points: QSpinBox = self._make_int(1, 30, config.cluster_min_points)
        self.confirm: QSpinBox = self._make_int(1, 30, config.frames_to_confirm)
        self._build()

    def _make_double(
        self, low: float, high: float, step: float, value: float, suffix: str
    ) -> QDoubleSpinBox:
        box: QDoubleSpinBox = QDoubleSpinBox()
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setValue(value)
        box.setSuffix(suffix)
        return box

    def _make_int(self, low: int, high: int, value: int) -> QSpinBox:
        box: QSpinBox = QSpinBox()
        box.setRange(low, high)
        box.setValue(value)
        return box

    def _build(self) -> None:
        form: QFormLayout = QFormLayout()
        form.addRow("Min SNR", self.min_snr)
        form.addRow("Max range", self.max_range)
        form.addRow("Max azimuth", self.max_azimuth)
        form.addRow("Cluster radius", self.cluster_eps)
        form.addRow("Min points/cluster", self.min_points)
        form.addRow("Frames to confirm", self.confirm)
        self.setLayout(form)

    def to_config(self, base: DetectionConfig) -> DetectionConfig:
        """Build a DetectionConfig from the current widget values."""
        return base.with_overrides(
            min_snr_db=self.min_snr.value(),
            max_range_m=self.max_range.value(),
            max_azimuth_deg=self.max_azimuth.value(),
            cluster_eps_m=self.cluster_eps.value(),
            cluster_min_points=self.min_points.value(),
            frames_to_confirm=self.confirm.value(),
        )


class MainWindow(QMainWindow):
    """Owns the acquisition worker and repaints on every report."""

    def __init__(self, source: FrameSource, config: DetectionConfig, title: str) -> None:
        super().__init__()
        self._config: DetectionConfig = config
        self._banner: QLabel = QLabel("NO OBJECT")
        self._status: QLabel = QLabel("Starting...")
        self._readout: ReadoutPanel = ReadoutPanel()
        self._controls: ControlPanel = ControlPanel(config)
        self._plot: RadarPlot = RadarPlot(config)
        self._worker: AcquisitionWorker = AcquisitionWorker(source, DetectionPipeline(config))
        self.setWindowTitle(title)
        self.resize(1180, 720)
        self._build_layout()
        self._connect()
        self._worker.start()

    def _build_layout(self) -> None:
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setStyleSheet(ABSENT_STYLE)
        side: QVBoxLayout = QVBoxLayout()
        side.addWidget(self._banner)
        side.addWidget(self._readout)
        side.addWidget(self._controls)
        side.addStretch(1)
        side.addWidget(self._status)
        side_widget: QWidget = QWidget()
        side_widget.setLayout(side)
        side_widget.setFixedWidth(380)
        row: QHBoxLayout = QHBoxLayout()
        row.addWidget(self._plot, stretch=1)
        row.addWidget(side_widget)
        root: QWidget = QWidget()
        root.setLayout(row)
        self.setCentralWidget(root)

    def _connect(self) -> None:
        self._worker.report_ready.connect(self._on_report)
        self._worker.failed.connect(self._on_failure)
        for box in (
            self._controls.min_snr,
            self._controls.max_range,
            self._controls.max_azimuth,
            self._controls.cluster_eps,
        ):
            box.valueChanged.connect(self._on_controls_changed)
        self._controls.min_points.valueChanged.connect(self._on_controls_changed)
        self._controls.confirm.valueChanged.connect(self._on_controls_changed)

    def _on_controls_changed(self) -> None:
        """Rebuild the pipeline with new gates. Cheap: it is a fresh object."""
        self._config = self._controls.to_config(self._config)
        self._worker.set_pipeline(DetectionPipeline(self._config))
        self._plot.rebuild_geometry(self._config)

    def _on_report(self, report: object) -> None:
        if not isinstance(report, DetectionReport):
            logger.warning("Ignoring unexpected payload on report_ready")
            return
        self._plot.update_report(report)
        self._update_banner(report)
        self._status.setText(
            f"Frame {report.frame_number} | raw {report.raw_point_count} pts "
            f"| gated {len(report.gated_points)} pts"
        )

    def _update_banner(self, report: DetectionReport) -> None:
        target: TargetCluster | None = report.primary
        if report.state is PresenceState.PRESENT and target is not None:
            self._banner.setText("OBJECT DETECTED")
            self._banner.setStyleSheet(PRESENT_STYLE)
            self._readout.show_target(target)
            return
        self._banner.setText("NO OBJECT")
        self._banner.setStyleSheet(ABSENT_STYLE)
        self._readout.clear()

    def _on_failure(self, message: str) -> None:
        self._status.setText(f"Stopped: {message}")
        QMessageBox.critical(self, "Sensor error", message)

    def closeEvent(self, a0: QCloseEvent | None) -> None:  # noqa: N802 (Qt override)
        """Stop acquisition before the window goes away, so no thread outlives it."""
        self._worker.shutdown()
        if a0 is not None:
            a0.accept()
