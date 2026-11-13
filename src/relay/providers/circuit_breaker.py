"""Layer 4 Circuit Breaker protecting Groq from repeated failures and rate-limit storms."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Literal

BreakerState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    rate_limit_threshold: int = 3
    cooldown_s: float = 10.0
    daily_cooldown_s: float = 300.0


class CircuitBreaker:
    """Thread/async-safe CLOSED -> OPEN -> HALF_OPEN circuit breaker for Layer 4."""

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self.cfg = config or CircuitBreakerConfig()
        self._lock = threading.Lock()
        self._state: BreakerState = "CLOSED"
        self._consecutive_failures = 0
        self._consecutive_rate_limits = 0
        self._opened_until_mono = 0.0
        self._daily_exhausted = False
        self._probe_in_flight = False
        self.trip_count = 0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def daily_exhausted(self) -> bool:
        with self._lock:
            return self._daily_exhausted

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == "OPEN" and not self._daily_exhausted:
            if time.monotonic() >= self._opened_until_mono:
                self._state = "HALF_OPEN"
                self._probe_in_flight = False

    def before_call(self) -> tuple[bool, BreakerState]:
        """Check whether a call to Groq is permitted right now."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == "CLOSED":
                return True, "CLOSED"
            if self._state == "OPEN":
                return False, "OPEN"
            # HALF_OPEN: allow exactly one probe call at a time
            if not self._probe_in_flight:
                self._probe_in_flight = True
                return True, "HALF_OPEN"
            return False, "HALF_OPEN"

    def record_success(self) -> None:
        with self._lock:
            self._state = "CLOSED"
            self._consecutive_failures = 0
            self._consecutive_rate_limits = 0
            self._daily_exhausted = False
            self._probe_in_flight = False

    def record_failure(
        self,
        *,
        is_rate_limit: bool = False,
        is_daily_limit: bool = False,
        retry_after_s: float | None = None,
    ) -> BreakerState:
        with self._lock:
            self._probe_in_flight = False
            if is_daily_limit:
                self._daily_exhausted = True
                self._trip(max(self.cfg.daily_cooldown_s, retry_after_s or 0.0))
                return self._state

            if is_rate_limit:
                self._consecutive_rate_limits += 1
                if (
                    self._state == "HALF_OPEN"
                    or self._consecutive_rate_limits >= self.cfg.rate_limit_threshold
                ):
                    cooldown = max(self.cfg.cooldown_s, retry_after_s or 0.0)
                    self._trip(cooldown)
            else:
                self._consecutive_failures += 1
                if (
                    self._state == "HALF_OPEN"
                    or self._consecutive_failures >= self.cfg.failure_threshold
                ):
                    self._trip(self.cfg.cooldown_s)
            return self._state

    def _trip(self, cooldown_s: float) -> None:
        if self._state != "OPEN":
            self.trip_count += 1
        self._state = "OPEN"
        self._opened_until_mono = time.monotonic() + max(0.1, cooldown_s)
        self._probe_in_flight = False

    def force_reset(self) -> None:
        with self._lock:
            self._state = "CLOSED"
            self._consecutive_failures = 0
            self._consecutive_rate_limits = 0
            self._daily_exhausted = False
            self._probe_in_flight = False
            self._opened_until_mono = 0.0
