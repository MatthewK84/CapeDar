"""Pull frames on a worker thread so the main loop never blocks.

Two problems are solved here, and both matter for headless operation.

Ctrl+C under Windows
    A blocking pyserial read does not return control to the interpreter, so
    KeyboardInterrupt is not delivered until the read finishes. Under Windows
    PowerShell that makes Ctrl+C feel dead. Reading on a daemon thread and
    consuming through ``queue.Queue.get(timeout=...)`` leaves the main thread
    free to take the signal immediately.

Silence detection
    ``RadarSensor.frames()`` yields nothing when the sensor stops talking; it
    does not return. A consumer looping over it directly can never notice the
    silence, so a signal line asserted before the sensor died would stay
    asserted forever. A queue with a timeout turns silence into an observable
    event.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .custom_types import RadarFrame
    from .sensor import FrameSource

logger: logging.Logger = logging.getLogger(__name__)

QUEUE_MAX_FRAMES: Final[int] = 64
JOIN_TIMEOUT_S: Final[float] = 2.0


class FrameReader:
    """Runs ``source.frames()`` on a daemon thread and buffers the results.

    The queue is bounded and drops the oldest frame when full. A slow consumer
    should fall behind in latency, never in memory.
    """

    def __init__(self, source: FrameSource, max_frames: int = QUEUE_MAX_FRAMES) -> None:
        if max_frames < 1:
            raise ValueError("max_frames must be >= 1")
        self._source: FrameSource = source
        self._queue: queue.Queue[RadarFrame] = queue.Queue(maxsize=max_frames)
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._error: BaseException | None = None
        self._dropped: int = 0

    @property
    def dropped_frames(self) -> int:
        """Frames discarded because the consumer could not keep up."""
        return self._dropped

    @property
    def error(self) -> BaseException | None:
        """Exception raised inside the reader thread, if any."""
        return self._error

    def start(self) -> None:
        """Begin reading. Safe to call once."""
        if self._thread is not None:
            raise RuntimeError("FrameReader is already started")
        self._thread = threading.Thread(target=self._pump, name="capedar-reader", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        """Thread body. Never raises; the exception is handed to the consumer."""
        try:
            for frame in self._source.frames():
                if self._stop_event.is_set():
                    break
                self._offer(frame)
        except BaseException as exc:
            self._error = exc
        finally:
            self._stop_event.set()

    def _offer(self, frame: RadarFrame) -> None:
        """Enqueue, dropping the oldest frame rather than blocking the reader."""
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            self._discard_oldest()
            self._dropped += 1
            self._put_or_drop(frame)

    def _discard_oldest(self) -> None:
        try:
            self._queue.get_nowait()
        except queue.Empty:
            logger.debug("Queue drained by consumer during drop")

    def _put_or_drop(self, frame: RadarFrame) -> None:
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            self._dropped += 1

    def next_frame(self, timeout_s: float) -> RadarFrame | None:
        """Return the next frame, or None if none arrived within the timeout.

        None is the signal that the stream has gone quiet. It is not an error;
        the caller decides what silence means.
        """
        try:
            return self._queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    @property
    def finished(self) -> bool:
        """True once the source stopped yielding and the buffer is drained."""
        return self._stop_event.is_set() and self._queue.empty()

    def stop(self) -> None:
        """Ask the reader to end and wait briefly for the thread. Never raises."""
        self._stop_event.set()
        try:
            self._source.stop()
        except Exception as exc:
            logger.warning("Error stopping source: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=JOIN_TIMEOUT_S)
            self._thread = None
