"""Unit tests verifying Layer 4 Circuit Breaker behavior (Fix 4)."""

import pytest

from relay.providers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


def test_circuit_breaker_closed_by_default():
    cb = CircuitBreaker()
    allowed, state = cb.before_call()
    assert allowed is True
    assert state == "CLOSED"
    assert cb.state == "CLOSED"


def test_circuit_breaker_trips_on_consecutive_failures():
    cfg = CircuitBreakerConfig(failure_threshold=2, cooldown_s=1.0)
    cb = CircuitBreaker(cfg)

    cb.record_failure(is_rate_limit=False)
    assert cb.state == "CLOSED"

    cb.record_failure(is_rate_limit=False)
    assert cb.state == "OPEN"

    allowed, state = cb.before_call()
    assert allowed is False
    assert state == "OPEN"


def test_circuit_breaker_trips_on_rate_limits():
    cfg = CircuitBreakerConfig(rate_limit_threshold=2, cooldown_s=0.5)
    cb = CircuitBreaker(cfg)

    cb.record_failure(is_rate_limit=True)
    assert cb.state == "CLOSED"

    cb.record_failure(is_rate_limit=True, retry_after_s=0.2)
    assert cb.state == "OPEN"


def test_circuit_breaker_daily_exhaustion():
    cfg = CircuitBreakerConfig(daily_cooldown_s=100.0)
    cb = CircuitBreaker(cfg)

    cb.record_failure(is_rate_limit=True, is_daily_limit=True)
    assert cb.state == "OPEN"
    assert cb.daily_exhausted is True

    allowed, state = cb.before_call()
    assert allowed is False
    assert state == "OPEN"


def test_circuit_breaker_half_open_probe_limiting(monkeypatch):
    cfg = CircuitBreakerConfig(failure_threshold=1, cooldown_s=0.1)
    cb = CircuitBreaker(cfg)

    cb.record_failure(is_rate_limit=False)
    assert cb.state == "OPEN"

    # Simulate time advancing past cooldown
    import time
    time.sleep(0.15)

    # First caller in HALF_OPEN is allowed as probe
    allowed1, state1 = cb.before_call()
    assert allowed1 is True
    assert state1 == "HALF_OPEN"

    # Concurrent second caller in HALF_OPEN is blocked
    allowed2, state2 = cb.before_call()
    assert allowed2 is False
    assert state2 == "HALF_OPEN"

    # Probe succeeds -> resets to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
