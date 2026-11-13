"""Layer 1 Load Generator and Scenario Runner driving real Groq and Stand-in experiments."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay.eval.grader import EvalQuestion, grade_answer
from relay.observability.bus import TelemetryBus
from relay.observability.models import SourceType
from relay.orchestrator.orchestrator import Orchestrator
from relay.pipeline.pipeline import AssistantPipeline


@dataclass
class ScenarioSpec:
    name: str
    source: SourceType
    num_requests: int
    concurrency_burst: int
    inter_arrival_ms: float
    retrieval_setting: str = "med_k5_rerank"
    patience_ms: float | None = None
    abandon_fraction: float = 0.0
    abandon_after_ms: float = 350.0


def load_questions(path: Path) -> list[EvalQuestion]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvalQuestion.from_dict(item) for item in raw]


async def run_retrieval_eval(
    *,
    orchestrator: Orchestrator,
    pipeline: AssistantPipeline,
    bus: TelemetryBus,
    questions: list[EvalQuestion],
    retrieval_setting: str,
    source: SourceType,
    scenario_prefix: str = "retrieval",
    inter_request_delay_s: float = 0.0,
) -> list[dict[str, Any]]:
    """Run all benchmark questions sequentially/politely for Table 3 retrieval comparison."""
    scenario = f"{scenario_prefix}_{retrieval_setting}"
    outcomes: list[dict[str, Any]] = []

    for idx, q in enumerate(questions):
        rid = f"{scenario}_{q.id}_{idx:02d}"
        res = await orchestrator.submit_request(
            request_id=rid,
            question=q.question,
            scenario=scenario,
            source=source,
            retrieval_setting=retrieval_setting,
            question_id=q.id,
            pipeline_fn=lambda ctx, cm, rset, ml, _q=q.question: pipeline.run(ctx, _q, cm, rset, ml),
        )
        grade = grade_answer(
            q,
            status=res.get("status", "error"),
            answer_text=res.get("answer", ""),
            citations=res.get("citations", []),
            is_refusal=bool(res.get("is_refusal", False)),
        )
        bus.update_grade(rid, grade)
        outcomes.append({"request_id": rid, "question_id": q.id, "grade": grade, **res})
        if inter_request_delay_s > 0 and idx + 1 < len(questions):
            await asyncio.sleep(inter_request_delay_s)
    return outcomes


async def run_load_scenario(
    *,
    spec: ScenarioSpec,
    orchestrator: Orchestrator,
    pipeline: AssistantPipeline,
    bus: TelemetryBus,
    questions: list[EvalQuestion],
) -> list[dict[str, Any]]:
    """Execute a shaped load scenario (steady, burst, overload, etc.) with optional client give-ups."""
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    answerable_qs = [q for q in questions if q.expected_type == "answerable"] or questions

    async def _one(idx: int, q: EvalQuestion) -> dict[str, Any]:
        rid = f"{spec.name}_{idx:03d}"
        should_abandon = spec.abandon_fraction > 0 and ((idx % max(1, int(round(1.0 / spec.abandon_fraction)))) == 0)

        async def _abandon_timer() -> None:
            await asyncio.sleep(spec.abandon_after_ms / 1000.0)
            orchestrator.cancel_request(rid, reason="client_abandoned")

        timer_task = asyncio.create_task(_abandon_timer()) if should_abandon else None
        try:
            res = await orchestrator.submit_request(
                request_id=rid,
                question=q.question,
                scenario=spec.name,
                source=spec.source,
                retrieval_setting=spec.retrieval_setting,
                question_id=q.id,
                patience_ms=spec.patience_ms,
                pipeline_fn=lambda ctx, cm, rset, ml, _q=q.question: pipeline.run(ctx, _q, cm, rset, ml),
            )
            return {"request_id": rid, **res}
        finally:
            if timer_task is not None and not timer_task.done():
                timer_task.cancel()

    for i in range(spec.num_requests):
        q = answerable_qs[i % len(answerable_qs)]
        tasks.append(asyncio.create_task(_one(i, q)))
        if spec.inter_arrival_ms > 0 and (i + 1) % max(1, spec.concurrency_burst) == 0:
            await asyncio.sleep(spec.inter_arrival_ms / 1000.0)

    return await asyncio.gather(*tasks, return_exceptions=True)
