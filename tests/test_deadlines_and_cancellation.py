"""Integration tests verifying end-to-end deadlines, retries, circuit breaker, and cancellation (Fixes 3, 4, 6)."""

import asyncio

import pytest

from relay.observability.bus import TelemetryBus
from relay.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from relay.providers.base import ChatMessage, ModelProvider, ProviderError, ProviderResponse, RateLimitHeaders
from relay.providers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


class FlakyProvider(ModelProvider):
    name = "groq"
    source_label = "real"

    def __init__(self, fail_times: int = 10, delay_s: float = 0.05, status_code: int = 429):
        self.calls = 0
        self.fail_times = fail_times
        self.delay_s = delay_s
        self.status_code = status_code

    async def stream_chat(self, messages, *, model, temperature=0.0, max_tokens=512, timeout_s=15.0, cancel_event=None):
        raise NotImplementedError

    async def chat(self, messages, *, model, temperature=0.0, max_tokens=512, timeout_s=15.0, cancel_event=None):
        self.calls += 1
        await asyncio.sleep(min(self.delay_s, timeout_s))
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("aborted")
        if self.calls <= self.fail_times:
            raise ProviderError(
                "transient failure",
                status_code=self.status_code,
                is_rate_limit=(self.status_code == 429),
                retry_after_s=0.05,
                rate_limit=RateLimitHeaders(),
            )
        return ProviderResponse(
            text="ok",
            tokens_in=10,
            tokens_out=5,
            provider_ttft_ms=10.0,
            total_ms=20.0,
            status_code=200,
            model=model,
            provider_name=self.name,
        )


class FastStandIn(ModelProvider):
    name = "stand-in"
    source_label = "stand-in"

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, *, model, temperature=0.0, max_tokens=512, timeout_s=15.0, cancel_event=None):
        raise NotImplementedError

    async def chat(self, messages, *, model, temperature=0.0, max_tokens=512, timeout_s=15.0, cancel_event=None):
        self.calls += 1
        return ProviderResponse(
            text="fallback ok",
            tokens_in=8,
            tokens_out=4,
            provider_ttft_ms=5.0,
            total_ms=8.0,
            status_code=200,
            model=model,
            provider_name=self.name,
        )


@pytest.mark.asyncio
async def test_single_end_to_end_deadline_never_resets_on_retries():
    bus = TelemetryBus()
    flaky = FlakyProvider(fail_times=10, delay_s=0.12, status_code=429)
    orch = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=350.0,
            per_call_timeout_ms=300.0,
            max_retries_per_call=5,
            base_backoff_ms=180.0,
            jitter=False,
        ),
        bus=bus,
        groq_provider=flaky,
    )

    async def dummy_pipe(ctx, call_model, rset, ml):
        await call_model("step_a_understand", [ChatMessage("user", "hi")], 50, 0.0)
        return {"status": "ok", "answer": "done"}

    res = await orch.submit_request(
        request_id="dl_1",
        question="What is d_model?",
        scenario="test",
        source="real",
        retrieval_setting="small_k3_norank",
        pipeline_fn=dummy_pipe,
    )
    assert res["status"] == "timeout"
    trace = bus.traces()[0]
    assert trace.completion_offset_ms is not None
    # Must stop within request_timeout_ms (+ tiny scheduling margin), never resetting deadline
    assert trace.completion_offset_ms < 550.0
    assert trace.retries <= 2


@pytest.mark.asyncio
async def test_client_abandonment_cancels_queued_and_in_flight_work():
    bus = TelemetryBus()
    slow = FlakyProvider(fail_times=0, delay_s=0.5)
    orch = Orchestrator(
        config=OrchestratorConfig(request_timeout_ms=5000.0, max_concurrency=1),
        bus=bus,
        groq_provider=slow,
    )

    async def two_step_pipe(ctx, call_model, rset, ml):
        await call_model("step_a_understand", [ChatMessage("user", "1")], 50, 0.0)
        ctx.check_active()
        await call_model("step_c_draft", [ChatMessage("user", "2")], 50, 0.0)
        return {"status": "ok", "answer": "done"}

    task1 = asyncio.create_task(
        orch.submit_request(
            request_id="req_hold",
            question="Q1",
            scenario="test",
            source="real",
            retrieval_setting="small_k3_norank",
            pipeline_fn=two_step_pipe,
        )
    )
    await asyncio.sleep(0.05)

    # req_cancel enters queue behind req_hold and is abandoned while queued
    task2 = asyncio.create_task(
        orch.submit_request(
            request_id="req_cancel",
            question="Q2",
            scenario="test",
            source="real",
            retrieval_setting="small_k3_norank",
            pipeline_fn=two_step_pipe,
        )
    )
    await asyncio.sleep(0.05)
    assert orch.cancel_request("req_cancel", reason="client_abandoned") is True

    res2 = await task2
    res1 = await task1
    assert res1["status"] == "ok"
    assert res2["status"] == "timeout"
    # Only req_hold ran the 2 model calls; req_cancel consumed 0 calls!
    assert slow.calls == 2


@pytest.mark.asyncio
async def test_circuit_breaker_routes_to_standin_when_fallback_allowed():
    bus = TelemetryBus()
    broken = FlakyProvider(fail_times=10, delay_s=0.01, status_code=503)
    standin = FastStandIn()
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, cooldown_s=5.0))
    orch = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=2000.0,
            max_retries_per_call=0,
            allow_standin_fallback=True,
        ),
        bus=bus,
        groq_provider=broken,
        standin_provider=standin,
        circuit_breaker=cb,
    )

    async def one_step(ctx, call_model, rset, ml):
        resp = await call_model("step_a_understand", [ChatMessage("user", "hi")], 50, 0.0)
        bus.record_first_verified_token(ctx.request_id)
        return {"status": "ok", "answer": resp.text}

    # Call 1 trips the circuit breaker
    r1 = await orch.submit_request(
        request_id="cb_1",
        question="Q1",
        scenario="test",
        source="real",
        retrieval_setting="small_k3_norank",
        pipeline_fn=one_step,
    )
    assert r1["status"] == "error"
    assert cb.state == "OPEN"

    # Call 2 routes cleanly to stand-in and updates trace source="stand-in"
    r2 = await orch.submit_request(
        request_id="cb_2",
        question="Q2",
        scenario="test",
        source="real",
        retrieval_setting="small_k3_norank",
        pipeline_fn=one_step,
    )
    assert r2["status"] == "ok"
    assert standin.calls == 1
    tr2 = [t for t in bus.traces() if t.request_id == "cb_2"][0]
    assert tr2.source == "stand-in"
    assert tr2.circuit_state == "OPEN"
