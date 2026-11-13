"""Unit tests verifying Step C loop/refusal bypass and Step D Response Gate (Fixes 1 & 2)."""

import json

import pytest

from relay.ingest.chunker import Chunk
from relay.ingest.retriever import RetrievedChunk
from relay.observability.bus import TelemetryBus, mono_ms
from relay.orchestrator.context import RequestContext
from relay.pipeline.pipeline import (
    SAFE_REFUSAL_EVIDENCE,
    SAFE_REFUSAL_VERIFY,
    AssistantPipeline,
    PipelineConfig,
)
from relay.providers.base import ProviderResponse


class FakeIndex:
    def search(self, query, *, top_k, rerank=False, hybrid_bm25=True, paper_filter=None):
        c = Chunk(
            chunk_id="1706.03762v7:p3:c1",
            paper_id="1706.03762v7",
            paper_title="Attention Is All You Need",
            short_name="Attention (Vaswani et al.)",
            version=7,
            page_num=3,
            section="3.2 Attention",
            text="Base model uses d_model = 512, h = 8 heads, d_k = d_v = 64.",
            word_count=12,
        )
        return [RetrievedChunk(chunk=c, score=0.95)]


class FakePreset:
    name = "small_k3_norank"
    top_k = 3
    rerank = False
    hybrid_bm25 = False


class FakeRegistry:
    def get_for_preset(self, preset_name):
        return FakeIndex(), FakePreset()


def _resp(text: str) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        tokens_in=50,
        tokens_out=20,
        provider_ttft_ms=5.0,
        total_ms=10.0,
        status_code=200,
        model="fake",
        provider_name="fake",
    )


def _ctx(rid: str, budget_ms: float = 10000.0) -> RequestContext:
    now = mono_ms()
    return RequestContext(
        request_id=rid,
        scenario="unit",
        source="stand-in",
        question_id="q",
        retrieval_setting="small_k3_norank",
        arrival_mono_ms=now,
        deadline_mono_ms=now + budget_ms,
    )


@pytest.mark.asyncio
async def test_step_c_refuse_bypasses_draft_and_step_d():
    bus = TelemetryBus()
    bus.record_arrival("r_refuse", "unit", "stand-in", 10000.0, "small_k3_norank")
    pipe = AssistantPipeline(registry=FakeRegistry(), bus=bus)

    steps_called: list[str] = []

    async def call_model(step_name, messages, max_tokens, temp):
        steps_called.append(step_name)
        if step_name == "step_a_understand":
            return _resp(json.dumps({"target_paper": None, "search_queries": ["wmt bleu"]}))
        if step_name == "step_c_decide":
            return _resp(json.dumps({"decision": "refuse", "reason": "out of scope"}))
        raise AssertionError(f"Should not call {step_name} on refuse!")

    res = await pipe.run(_ctx("r_refuse"), "Unanswerable?", call_model, "small_k3_norank", max_loops=2)
    assert res["is_refusal"] is True
    assert res["answer"] == SAFE_REFUSAL_EVIDENCE
    assert steps_called == ["step_a_understand", "step_c_decide"]


@pytest.mark.asyncio
async def test_step_c_retrieve_more_loops_back_to_step_b():
    bus = TelemetryBus()
    bus.record_arrival("r_loop", "unit", "stand-in", 10000.0, "small_k3_norank")
    pipe = AssistantPipeline(registry=FakeRegistry(), bus=bus)

    decide_count = 0

    async def call_model(step_name, messages, max_tokens, temp):
        nonlocal decide_count
        if step_name == "step_a_understand":
            return _resp(json.dumps({"target_paper": None, "search_queries": ["q1"]}))
        if step_name == "step_c_decide":
            decide_count += 1
            if decide_count == 1:
                return _resp(json.dumps({"decision": "retrieve_more", "followup_query": "d_model 512"}))
            return _resp(json.dumps({"decision": "answer", "reason": "ok"}))
        if step_name == "step_c_draft":
            return _resp("d_model is 512 [Attention (Vaswani et al.), 1706.03762v7, 3.2 Attention, p.3].")
        if step_name == "step_d_verify":
            return _resp(json.dumps({"verdict": "PASS", "unsupported_claims": [], "feedback": ""}))
        raise AssertionError(step_name)

    res = await pipe.run(_ctx("r_loop"), "What is d_model?", call_model, "small_k3_norank", max_loops=2)
    assert res["verified"] is True
    assert decide_count == 2
    step_b_spans = [s for s in bus.traces()[0].steps if s.step == "step_b_retrieve"]
    assert len(step_b_spans) == 2


@pytest.mark.asyncio
async def test_response_gate_triggers_one_bounded_correction_then_passes():
    bus = TelemetryBus()
    bus.record_arrival("r_corr", "unit", "stand-in", 10000.0, "small_k3_norank")
    pipe = AssistantPipeline(registry=FakeRegistry(), bus=bus, config=PipelineConfig(max_correction_attempts=1))

    draft_calls = 0

    async def call_model(step_name, messages, max_tokens, temp):
        nonlocal draft_calls
        if step_name == "step_a_understand":
            return _resp(json.dumps({"target_paper": None, "search_queries": ["d_model"]}))
        if step_name == "step_c_decide":
            return _resp(json.dumps({"decision": "answer"}))
        if step_name in ("step_c_draft", "correction_draft"):
            draft_calls += 1
            if draft_calls == 1:
                # First draft omits citation -> deterministic gate catches it!
                return _resp("d_model is 512.")
            return _resp("d_model is 512 [Attention (Vaswani et al.), 1706.03762v7, 3.2 Attention, p.3].")
        if step_name == "step_d_verify":
            return _resp(json.dumps({"verdict": "PASS", "unsupported_claims": [], "feedback": ""}))
        raise AssertionError(step_name)

    res = await pipe.run(_ctx("r_corr"), "What is d_model?", call_model, "small_k3_norank", max_loops=1)
    assert res["verified"] is True
    assert res["is_refusal"] is False
    assert draft_calls == 2


@pytest.mark.asyncio
async def test_response_gate_refuses_if_correction_also_fails():
    bus = TelemetryBus()
    bus.record_arrival("r_fail_gate", "unit", "stand-in", 10000.0, "small_k3_norank")
    pipe = AssistantPipeline(registry=FakeRegistry(), bus=bus, config=PipelineConfig(max_correction_attempts=1))

    async def call_model(step_name, messages, max_tokens, temp):
        if step_name == "step_a_understand":
            return _resp(json.dumps({"target_paper": None, "search_queries": ["d_model"]}))
        if step_name == "step_c_decide":
            return _resp(json.dumps({"decision": "answer"}))
        if step_name in ("step_c_draft", "correction_draft"):
            return _resp("d_model is 512 [Attention (Vaswani et al.), 1706.03762v7, 3.2 Attention, p.3].")
        if step_name == "step_d_verify":
            return _resp(json.dumps({"verdict": "FAIL", "unsupported_claims": ["bad"], "feedback": "unsupported"}))
        raise AssertionError(step_name)

    res = await pipe.run(_ctx("r_fail_gate"), "What is d_model?", call_model, "small_k3_norank", max_loops=1)
    assert res["is_refusal"] is True
    assert res["verified"] is False
    assert res["answer"] == SAFE_REFUSAL_VERIFY
