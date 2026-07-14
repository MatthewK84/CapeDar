"""Top-down (x/y) radar view: FOV wedge, range rings, points, target boxes.

pyqtgraph ships no type stubs, so its widgets are ``Any`` to mypy. This widget
composes a PlotWidget rather than subclassing one, which keeps
``disallow_subclassing_any`` satisfied and confines the untyped surface to a
private attribute.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Final

import pyqtgraph as pg
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

if TYPE_CHECKING:
    from .config import DetectionConfig
    from .types import DetectedPoint, DetectionReport, TargetCluster

WEDGE_SEGMENTS: Final[int] = 48
RING_STEP_M: Final[float] = 1.0
POINT_COLOR: Final[str] = "#4aa3ff"
TARGET_COLOR: Final[str] = "#ff4d4d"
GRID_COLOR: Final[str] = "#3a3a3a"
BACKGROUND: Final[str] = "#101010"


def arc_vertices(radius_m: float, half_angle_deg: float) -> tuple[list[float], list[float]]:
    """Arc of constant range spanning the field of view."""
    xs: list[float] = []
    ys: list[float] = []
    for step in range(WEDGE_SEGMENTS + 1):
        angle_deg: float = -half_angle_deg + (2.0 * half_angle_deg * step / WEDGE_SEGMENTS)
        angle_rad: float = math.radians(angle_deg)
        xs.append(radius_m * math.sin(angle_rad))
        ys.append(radius_m * math.cos(angle_rad))
    return xs, ys


def wedge_vertices(max_range_m: float, half_angle_deg: float) -> tuple[list[float], list[float]]:
    """Closed field-of-view boundary, starting and ending at the sensor origin."""
    xs, ys = arc_vertices(max_range_m, half_angle_deg)
    return [0.0, *xs, 0.0], [0.0, *ys, 0.0]


class RadarPlot(QWidget):
    """Bird's-eye plot. +x is right of boresight, +y is downrange."""

    def __init__(self, config: DetectionConfig) -> None:
        super().__init__()
        self._config: DetectionConfig = config
        self._plot: Any = pg.PlotWidget(background=BACKGROUND)
        self._scatter: Any = None
        self._target_boxes: list[Any] = []
        layout: QVBoxLayout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot)
        self.setLayout(layout)
        self._draw_all()

    def _draw_all(self) -> None:
        self._configure_axes()
        self._draw_static_geometry()
        self._scatter = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(QColor(POINT_COLOR)), pen=None)
        self._plot.addItem(self._scatter)
        self._target_boxes = []

    def _configure_axes(self) -> None:
        span: float = self._config.max_range_m
        self._plot.setAspectLocked(True)
        self._plot.setLabel("bottom", "Cross-range", units="m")
        self._plot.setLabel("left", "Downrange", units="m")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._plot.setXRange(-span * 0.8, span * 0.8)
        self._plot.setYRange(0.0, span * 1.05)

    def _draw_static_geometry(self) -> None:
        half_angle: float = self._config.max_azimuth_deg
        xs, ys = wedge_vertices(self._config.max_range_m, half_angle)
        self._plot.addItem(pg.PlotDataItem(xs, ys, pen=pg.mkPen(GRID_COLOR, width=2)))
        radius: float = RING_STEP_M
        while radius <= self._config.max_range_m:
            ring_x, ring_y = arc_vertices(radius, half_angle)
            self._plot.addItem(pg.PlotDataItem(ring_x, ring_y, pen=pg.mkPen(GRID_COLOR, width=1)))
            radius += RING_STEP_M

    def rebuild_geometry(self, config: DetectionConfig) -> None:
        """Redraw static geometry after a live gate change."""
        self._config = config
        self._plot.clear()
        self._draw_all()

    def update_report(self, report: DetectionReport) -> None:
        """Redraw points and target boxes for one frame."""
        points: tuple[DetectedPoint, ...] = report.gated_points
        self._scatter.setData([p.x_m for p in points], [p.y_m for p in points])
        self._clear_boxes()
        for target in report.targets:
            self._add_box(target)

    def _clear_boxes(self) -> None:
        for box in self._target_boxes:
            self._plot.removeItem(box)
        self._target_boxes = []

    def _add_box(self, target: TargetCluster) -> None:
        left: float = target.centroid_x_m - target.size.width_m / 2.0
        bottom: float = target.centroid_y_m - target.size.depth_m / 2.0
        box: Any = pg.QtWidgets.QGraphicsRectItem(
            left, bottom, target.size.width_m, target.size.depth_m
        )
        box.setPen(pg.mkPen(TARGET_COLOR, width=2))
        self._plot.addItem(box)
        self._target_boxes.append(box)
