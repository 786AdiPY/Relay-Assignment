"""Layer 2 quota tracker and adaptive retrieval degradation policy."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from relay.providers.base import RateLimitHeaders

PressureLevel = Literal["normal", "near_limit", "daily_exhausted"]


@dataclass
class QuotaConfig:
    rpm_soft_ratio: float = 0.80
    tpm_soft_ratio: float = 0.80
    assumed_rpm: int = 30
    assumed_tpm: int = 6000
    recent_429_window_s: float = 15.0


class QuotaTracker:
    """Tracks real-time provider headers, minute-window usage, and daily exhaustion."""

    def __init__(self, config: QuotaConfig | None = None) -> None:
        self.cfg = config or QuotaConfig()
        self._lock = threading.Lock()
        self._recent_429_mono: deque[float] = deque()
        self._minute_reqs: deque[float] = deque()
        self._minute_tokens: deque[tuple[float, int]] = deque()
        self._last_headers = RateLimitHeaders()
        self._daily_exhausted = False
        self._daily_reset_mono: float | None = None

    def observe_headers(self, rl: RateLimitHeaders) -> None:
        with self._lock:
            self._last_headers = rl

    def record_usage(self, tokens_total: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._minute_reqs.append(now)
            self._minute_tokens.append((now, max(0, tokens_total)))
            self._prune(now)

    def record_rate_limit(self, is_daily: bool, retry_after_s: float | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            self._recent_429_mono.append(now)
            if is_daily:
                self._daily_exhausted = True
                self._daily_reset_mono = now + (retry_after_s or 3600.0)
            self._prune(now)

    def mark_daily_exhausted(self, reset_s: float = 3600.0) -> None:
        with self._lock:
            self._daily_exhausted = True
            self._daily_reset_mono = time.monotonic() + reset_s

    def reset(self) -> None:
        with self._lock:
            self._recent_429_mono.clear()
            self._minute_reqs.clear()
            self._minute_tokens.clear()
            self._last_headers = RateLimitHeaders()
            self._daily_exhausted = False
            self._daily_reset_mono = None

    def _prune(self, now: float) -> None:
        while self._minute_reqs and now - self._minute_reqs[0] > 60.0:
            self._minute_reqs.popleft()
        while self._minute_tokens and now - self._minute_tokens[0][0] > 60.0:
            self._minute_tokens.popleft()
        while self._recent_429_mono and now - self._recent_429_mono[0] > self.cfg.recent_429_window_s:
            self._recent_429_mono.popleft()
        if self._daily_exhausted and self._daily_reset_mono is not None and now >= self._daily_reset_mono:
            self._daily_exhausted = False
            self._daily_reset_mono = None

    def pressure_level(self) -> PressureLevel:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if self._daily_exhausted:
                return "daily_exhausted"

            h = self._last_headers
            if h.limit_tokens and h.remaining_tokens is not None:
                if h.remaining_tokens <= h.limit_tokens * (1.0 - self.cfg.tpm_soft_ratio):
                    return "near_limit"

            rpm_limit = self.cfg.assumed_rpm
            tpm_limit = h.limit_tokens or self.cfg.assumed_tpm
            if len(self._minute_reqs) >= rpm_limit * self.cfg.rpm_soft_ratio:
                return "near_limit"
            if sum(t for _, t in self._minute_tokens) >= tpm_limit * self.cfg.tpm_soft_ratio:
                return "near_limit"
            if self._recent_429_mono:
                return "near_limit"
            return "normal"


def choose_adaptive_retrieval(base_setting: str, pressure: PressureLevel) -> tuple[str, int, str]:
    """Return (effective_retrieval_setting, max_retrieval_loops, degradation_mode)."""
    if pressure == "normal":
        return base_setting, 2, "none"

    degrade_map = {
        "med_k5_rerank": "med_k3_degraded",
        "large_k8_norank": "small_k3_norank",
        "small_k3_norank": "small_k3_norank",
        "med_k3_degraded": "med_k3_degraded",
    }
    return degrade_map.get(base_setting, "med_k3_degraded"), 1, "near_limit_shed_context"
