"""Temporal hysteresis.

Third anti-phantom stage. A single frame with a cluster is not presence, and a
single frame without one is not absence. Requiring N consecutive confirmations
to latch on and M to latch off suppresses both one-frame CFAR flashes and the
dropouts that occur when a person holds still.
"""

from __future__ import annotations

from .types import PresenceState


class PresenceTracker:
    """Confirm/clear counter pair driving a two-state machine.

    State is per-instance; construct one tracker per sensor stream.
    """

    def __init__(self, frames_to_confirm: int, frames_to_clear: int) -> None:
        if frames_to_confirm < 1 or frames_to_clear < 1:
            raise ValueError("frames_to_confirm and frames_to_clear must be >= 1")
        self._frames_to_confirm: int = frames_to_confirm
        self._frames_to_clear: int = frames_to_clear
        self._state: PresenceState = PresenceState.ABSENT
        self._hits: int = 0
        self._misses: int = 0

    @property
    def state(self) -> PresenceState:
        return self._state

    @property
    def is_present(self) -> bool:
        return self._state is PresenceState.PRESENT

    def reset(self) -> None:
        self._state = PresenceState.ABSENT
        self._hits = 0
        self._misses = 0

    def update(self, has_target: bool) -> PresenceState:
        """Advance one frame and return the resulting state."""
        if has_target:
            return self._on_hit()
        return self._on_miss()

    def _on_hit(self) -> PresenceState:
        self._misses = 0
        self._hits += 1
        if self._hits >= self._frames_to_confirm:
            self._state = PresenceState.PRESENT
        return self._state

    def _on_miss(self) -> PresenceState:
        self._hits = 0
        self._misses += 1
        if self._misses >= self._frames_to_clear:
            self._state = PresenceState.ABSENT
        return self._state
