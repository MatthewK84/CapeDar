"""Temporal hysteresis.

Third anti-phantom stage. A single frame with a cluster is not presence, and a
single frame without one is not absence. Requiring N consecutive confirmations
to latch on and M to latch off suppresses both one-frame CFAR flashes and the
dropouts that occur when a person holds still.
"""

from __future__ import annotations

from .custom_types import PresenceState


class Hysteresis:
    """Generic confirm/clear latch.

    Returns True only after ``frames_to_confirm`` consecutive hits, and stays
    True until ``frames_to_clear`` consecutive misses. Shared by the presence
    tracker and the multi-object occupancy tracker so both latches behave
    identically and are tested once.
    """

    def __init__(self, frames_to_confirm: int, frames_to_clear: int) -> None:
        if frames_to_confirm < 1 or frames_to_clear < 1:
            raise ValueError("frames_to_confirm and frames_to_clear must be >= 1")
        self._frames_to_confirm: int = frames_to_confirm
        self._frames_to_clear: int = frames_to_clear
        self._latched: bool = False
        self._hits: int = 0
        self._misses: int = 0

    @property
    def latched(self) -> bool:
        return self._latched

    def reset(self) -> None:
        self._latched = False
        self._hits = 0
        self._misses = 0

    def update(self, hit: bool) -> bool:
        """Advance one frame and return the latch state."""
        if hit:
            return self._on_hit()
        return self._on_miss()

    def _on_hit(self) -> bool:
        self._misses = 0
        self._hits += 1
        if self._hits >= self._frames_to_confirm:
            self._latched = True
        return self._latched

    def _on_miss(self) -> bool:
        self._hits = 0
        self._misses += 1
        if self._misses >= self._frames_to_clear:
            self._latched = False
        return self._latched


class PresenceTracker:
    """Confirm/clear counter pair driving a two-state machine.

    State is per-instance; construct one tracker per sensor stream.
    """

    def __init__(self, frames_to_confirm: int, frames_to_clear: int) -> None:
        self._hysteresis: Hysteresis = Hysteresis(frames_to_confirm, frames_to_clear)

    @property
    def state(self) -> PresenceState:
        if self._hysteresis.latched:
            return PresenceState.PRESENT
        return PresenceState.ABSENT

    @property
    def is_present(self) -> bool:
        return self._hysteresis.latched

    def reset(self) -> None:
        self._hysteresis.reset()

    def update(self, has_target: bool) -> PresenceState:
        """Advance one frame and return the resulting state."""
        self._hysteresis.update(has_target)
        return self.state
