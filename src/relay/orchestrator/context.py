"""Request execution context enforcing single end-to-end deadlines and cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from relay.observability.bus import mono_ms
from relay.observability.models import SourceType


class DeadlineExceededError(TimeoutError):
    """Raised when the single end-to-end request deadline is exhausted."""


class RequestCancelledError(asyncio.CancelledError):
    """Raised when a client abandons or shutdown cancels a pending request."""


@dataclass
class RequestContext:
    """Immutable deadline and cancellation state shared across Layers 1-4."""

    request_id: str
    scenario: str
    source: SourceType
    question_id: str
    retrieval_setting: str
    arrival_mono_ms: float
    deadline_mono_ms: float
    allow_standin_fallback: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_reason: str | None = None

    def remaining_ms(self) -> float:
        """Milliseconds remaining under the single end-to-end deadline (never resets)."""
        return max(0.0, self.deadline_mono_ms - mono_ms())

    def remaining_s(self) -> float:
        return self.remaining_ms() / 1000.0

    def is_expired(self) -> bool:
        return mono_ms() >= self.deadline_mono_ms

    def cancel(self, reason: str) -> None:
        if not self.cancel_event.is_set():
            self.cancel_reason = reason
            self.cancel_event.set()

    def check_active(self) -> None:
        """Raise immediately if cancelled or end-to-end deadline has passed."""
        if self.cancel_event.is_set():
            raise RequestCancelledError(self.cancel_reason or "client_abandoned")
        if self.is_expired():
            raise DeadlineExceededError("End-to-end request deadline exceeded")
