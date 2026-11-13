"""Layer 3 AI Assistant Pipeline implementing Steps A -> B -> C (loop/refuse/draft) -> D -> Response Gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from relay.ingest.retriever import IndexRegistry, RetrievedChunk
from relay.observability.bus import TelemetryBus, mono_ms
from relay.orchestrator.context import RequestContext
from relay.orchestrator.orchestrator import ModelCallFn
from relay.pipeline.prompts import (
    build_step_a_messages,
    build_step_c_decide_messages,
    build_step_c_draft_messages,
    build_step_d_verify_messages,
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_CIT_RE = re.compile(r"\[[^\]]+\]")

SAFE_REFUSAL_EVIDENCE = (
    "I cannot answer this from the three provided research papers "
    "(Attention Is All You Need, Moshi, and Dynamic Chunking / H-Net): "
    "the retrieved passages do not contain verified evidence for this question."
)
SAFE_REFUSAL_VERIFY = (
    "I could not verify a fully grounded, cited answer from the three library papers "
    "within the remaining latency/verification budget, so I am withholding an unverified reply."
)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJ_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _merge_unique_chunks(batches: list[list[RetrievedChunk]], limit: int) -> list[RetrievedChunk]:
    best: dict[str, RetrievedChunk] = {}
    for batch in batches:
        for rc in batch:
            cid = rc.chunk.chunk_id
            if cid not in best or rc.score > best[cid].score:
                best[cid] = rc
    return sorted(best.values(), key=lambda x: x.score, reverse=True)[:limit]


def _check_citations_against_chunks(draft: str, chunks: list[RetrievedChunk]) -> tuple[bool, list[str], str]:
    """Deterministically verify cited labels/papers match actually retrieved chunks."""
    found = _CIT_RE.findall(draft)
    if not found:
        return False, [], "Draft contains no bracketed citations."

    valid_labels = {rc.chunk.citation_label() for rc in chunks}
    valid_refs = {(rc.chunk.paper_id, f"p.{rc.chunk.page_num}") for rc in chunks}
    matched: list[str] = []
    unmatched: list[str] = []

    for cit in found:
        if cit in valid_labels or any(pid in cit and pg in cit for pid, pg in valid_refs):
            matched.append(cit)
        else:
            unmatched.append(cit)

    if not matched:
        return False, [], f"No citations match retrieved passages (found {found})."
    if unmatched:
        return False, matched, f"Found unrecognized/ungrounded citations: {unmatched}"
    return True, matched, ""


@dataclass
class PipelineConfig:
    min_loop_budget_ms: float = 1800.0
    min_correction_budget_ms: float = 2200.0
    max_correction_attempts: int = 1


class AssistantPipeline:
    """Four-step RAG pipeline with bounded loops, citation gate, and cancellation checks."""

    def __init__(
        self,
        *,
        registry: IndexRegistry,
        bus: TelemetryBus,
        config: PipelineConfig | None = None,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.cfg = config or PipelineConfig()

    async def run(
        self,
        ctx: RequestContext,
        question: str,
        call_model: ModelCallFn,
        retrieval_setting: str,
        max_loops: int,
    ) -> dict[str, Any]:
        """Execute Steps A -> B -> C [loop/refuse/draft] -> D -> Response Gate."""
        ctx.check_active()
        index, preset = self.registry.get_for_preset(retrieval_setting)

        # --- Step A: Query understanding ---
        a_start = mono_ms()
        a_resp = await call_model("step_a_understand", build_step_a_messages(question), 160, 0.0)
        a_end = mono_ms()
        parsed_a = _extract_json(a_resp.text)
        target_paper = parsed_a.get("target_paper")
        queries = parsed_a.get("search_queries") or [question]
        if not isinstance(queries, list) or not queries:
            queries = [question]
        queries = [str(q) for q in queries[:2]]
        self.bus.record_step(
            ctx.request_id,
            "step_a_understand",
            a_start,
            a_end,
            meta={"target_paper": target_paper, "queries": queries},
        )

        # --- Step B <-> Step C loop ---
        accumulated: list[RetrievedChunk] = []
        decision = "retrieve_more"
        active_queries = queries
        effective_filter = target_paper if isinstance(target_paper, str) else None

        for loop_idx in range(max(1, max_loops)):
            ctx.check_active()
            b_start = mono_ms()
            batches = [
                index.search(
                    q,
                    top_k=preset.top_k,
                    rerank=preset.rerank,
                    hybrid_bm25=preset.hybrid_bm25,
                    paper_filter=effective_filter if loop_idx == 0 else None,
                )
                for q in active_queries
            ]
            accumulated = _merge_unique_chunks([accumulated, *batches], limit=preset.top_k)
            b_end = mono_ms()
            self.bus.record_step(
                ctx.request_id,
                "step_b_retrieve",
                b_start,
                b_end,
                loop_idx=loop_idx,
                meta={"preset": preset.name, "chunks": len(accumulated), "queries": active_queries},
            )

            ctx.check_active()
            c_dec_start = mono_ms()
            c_dec_resp = await call_model(
                "step_c_decide",
                build_step_c_decide_messages(question, accumulated),
                120,
                0.0,
            )
            c_dec_end = mono_ms()
            parsed_c = _extract_json(c_dec_resp.text)
            decision = str(parsed_c.get("decision", "answer")).strip().lower()
            followup = str(parsed_c.get("followup_query", "")).strip()
            self.bus.record_step(
                ctx.request_id,
                "step_c_decide",
                c_dec_start,
                c_dec_end,
                loop_idx=loop_idx,
                meta={"decision": decision, "reason": parsed_c.get("reason", "")},
            )

            if decision == "refuse":
                # Fix 2: Refuse bypasses drafting & Step D verification
                ref_start = mono_ms()
                self.bus.record_first_verified_token(ctx.request_id)
                ref_end = mono_ms()
                self.bus.record_step(ctx.request_id, "safe_refusal", ref_start, ref_end, loop_idx=loop_idx)
                return {
                    "status": "ok",
                    "answer": SAFE_REFUSAL_EVIDENCE,
                    "citations": [],
                    "is_refusal": True,
                    "verified": True,
                }

            if decision == "answer":
                break

            # decision == "retrieve_more": return to Step B only if loops & deadline remain
            if loop_idx + 1 >= max_loops or ctx.remaining_ms() < self.cfg.min_loop_budget_ms:
                break
            active_queries = [followup or question]
            effective_filter = None

        if not accumulated:
            ref_start = mono_ms()
            self.bus.record_first_verified_token(ctx.request_id)
            ref_end = mono_ms()
            self.bus.record_step(ctx.request_id, "safe_refusal", ref_start, ref_end)
            return {
                "status": "ok",
                "answer": SAFE_REFUSAL_EVIDENCE,
                "citations": [],
                "is_refusal": True,
                "verified": True,
            }

        # --- Step C (Draft) -> Step D (Verify) -> Final Response Gate (+ max 1 correction) ---
        feedback = ""
        for attempt in range(self.cfg.max_correction_attempts + 1):
            ctx.check_active()
            draft_step = "step_c_draft" if attempt == 0 else "correction_draft"
            d_start = mono_ms()
            draft_resp = await call_model(
                draft_step,
                build_step_c_draft_messages(question, accumulated, correction_feedback=feedback),
                380,
                0.0,
            )
            d_end = mono_ms()
            draft_text = draft_resp.text.strip()
            self.bus.record_step(ctx.request_id, draft_step, d_start, d_end, loop_idx=attempt)

            if "INSUFFICIENT_EVIDENCE" in draft_text:
                ref_start = mono_ms()
                self.bus.record_first_verified_token(ctx.request_id)
                ref_end = mono_ms()
                self.bus.record_step(ctx.request_id, "safe_refusal", ref_start, ref_end, loop_idx=attempt)
                return {
                    "status": "ok",
                    "answer": SAFE_REFUSAL_EVIDENCE,
                    "citations": [],
                    "is_refusal": True,
                    "verified": True,
                }

            ctx.check_active()
            v_start = mono_ms()
            cit_ok, matched_cits, cit_err = _check_citations_against_chunks(draft_text, accumulated)
            verdict = "FAIL"
            verify_feedback = cit_err
            if cit_ok:
                v_resp = await call_model(
                    "step_d_verify",
                    build_step_d_verify_messages(question, draft_text, accumulated),
                    140,
                    0.0,
                )
                parsed_v = _extract_json(v_resp.text)
                verdict = str(parsed_v.get("verdict", "FAIL")).strip().upper()
                verify_feedback = str(parsed_v.get("feedback", "") or parsed_v.get("unsupported_claims", ""))
            v_end = mono_ms()
            self.bus.record_step(
                ctx.request_id,
                "step_d_verify",
                v_start,
                v_end,
                loop_idx=attempt,
                meta={"verdict": verdict, "cit_ok": cit_ok, "feedback": verify_feedback},
            )

            # Fix 1: Final Response Gate
            gate_start = mono_ms()
            if cit_ok and verdict == "PASS":
                self.bus.record_first_verified_token(ctx.request_id)
                gate_end = mono_ms()
                self.bus.record_step(
                    ctx.request_id,
                    "response_gate",
                    gate_start,
                    gate_end,
                    loop_idx=attempt,
                    meta={"passed": True},
                )
                return {
                    "status": "ok",
                    "answer": draft_text,
                    "citations": matched_cits,
                    "is_refusal": False,
                    "verified": True,
                }

            can_correct = (
                attempt < self.cfg.max_correction_attempts
                and ctx.remaining_ms() >= self.cfg.min_correction_budget_ms
            )
            gate_end = mono_ms()
            self.bus.record_step(
                ctx.request_id,
                "response_gate",
                gate_start,
                gate_end,
                loop_idx=attempt,
                meta={"passed": False, "retrying_correction": can_correct},
            )
            if not can_correct:
                break
            feedback = verify_feedback or "Ensure every claim is supported and accurately cited."

        # Gate fallback: safe refusal after bounded correction exhausted or deadline tight
        ref_start = mono_ms()
        self.bus.record_first_verified_token(ctx.request_id)
        ref_end = mono_ms()
        self.bus.record_step(ctx.request_id, "gate_safe_refusal", ref_start, ref_end)
        return {
            "status": "ok",
            "answer": SAFE_REFUSAL_VERIFY,
            "citations": [],
            "is_refusal": True,
            "verified": False,
        }
