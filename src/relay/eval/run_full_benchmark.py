"""End-to-end benchmark runner: runs retrieval sweep, load scenarios, flushes CSV, and formats report."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from relay.config import QUESTIONS_PATH, RETRIEVAL_PRESETS, RUNS_DIR
from relay.eval.grader import EvalQuestion
from relay.eval.runner import ScenarioSpec, load_questions, run_load_scenario, run_retrieval_eval
from relay.eval.tables import (
    analyze_slow_trace,
    compute_retrieval_table,
    compute_scenario_tables,
    load_results_csv,
    load_traces_jsonl,
)
from relay.ingest.retriever import IndexRegistry
from relay.observability.bus import TelemetryBus
from relay.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from relay.pipeline.pipeline import AssistantPipeline
from relay.providers.circuit_breaker import CircuitBreaker
from relay.providers.standin_client import StandInProvider
from relay.providers.standin_server import StandInConfig, create_standin_app


def format_markdown_table(headers: list[str], rows: list[dict[str, str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


async def run_all(output_dir: Path = RUNS_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_file = output_dir / "results.csv"
    if csv_file.exists():
        csv_file.unlink()
    jsonl_file = output_dir / "traces.jsonl"
    if jsonl_file.exists():
        jsonl_file.unlink()

    bus = TelemetryBus(output_dir=output_dir)
    questions = load_questions(QUESTIONS_PATH)
    registry = IndexRegistry()
    pipeline = AssistantPipeline(registry=registry, bus=bus)

    # Pre-build all 3 indexes so retrieval sweep measures search/rerank without disk build overhead
    print("[Benchmark] Pre-building FAISS/BM25 indexes for retrieval presets...")
    for preset_name in ("small_k3_norank", "med_k5_rerank", "large_k8_norank"):
        idx, _ = registry.get_for_preset(preset_name)
        print(f"  Preset {preset_name}: {len(idx.chunks)} chunks indexed")

    # -------------------------------------------------------------
    # 1. Table 3: Retrieval Sweep (>= 3 settings on 16 questions)
    # -------------------------------------------------------------
    print("\n[Benchmark] --- Step 1: Running Table 3 Retrieval Sweep (16 questions x 3 presets) ---")
    standin_app, standin_state = create_standin_app(
        StandInConfig(base_ttft_ms=120.0, inter_token_ms=3.0, tpm_limit=500000, rpm_limit=500)
    )
    standin_client = StandInProvider(app=standin_app)

    orch_eval = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=15000.0,
            max_concurrency=4,
            enable_adaptive_degradation=False,  # Keep exact preset parameters for Table 3 comparison
        ),
        bus=bus,
        standin_provider=standin_client,
    )

    for preset_name in ("small_k3_norank", "med_k5_rerank", "large_k8_norank"):
        print(f"  Evaluating retrieval preset: {preset_name}...")
        await run_retrieval_eval(
            orchestrator=orch_eval,
            pipeline=pipeline,
            bus=bus,
            questions=questions,
            retrieval_setting=preset_name,
            source="stand-in",
            inter_request_delay_s=0.01,
        )

    # -------------------------------------------------------------
    # 2. Table 2a / 2b: Load Scenarios
    # -------------------------------------------------------------
    print("\n[Benchmark] --- Step 2: Running Table 2a/2b Load Scenarios ---")

    # Real Groq Baseline (Modest real requests to establish real-world numbers)
    from relay.config import get_groq_api_key
    from relay.providers.groq import GroqProvider
    groq_key = get_groq_api_key()
    if groq_key:
        print("  Running Scenario: real_steady (on live Groq free tier)...")
        groq_prov = GroqProvider(api_key=groq_key)
        orch_real = Orchestrator(
            config=OrchestratorConfig(request_timeout_ms=18000.0, max_concurrency=2),
            bus=bus,
            groq_provider=groq_prov,
        )
        await run_load_scenario(
            spec=ScenarioSpec(
                name="real_steady",
                source="real",
                num_requests=4,
                concurrency_burst=1,
                inter_arrival_ms=1500.0,
                retrieval_setting="small_k3_norank",
            ),
            orchestrator=orch_real,
            pipeline=pipeline,
            bus=bus,
            questions=questions[:4],
        )

    # Scenario A: Steady (steady offered rate within capacity)
    print("  Running Scenario: steady...")
    standin_state.reset(StandInConfig(base_ttft_ms=130.0, rpm_limit=60, max_concurrent=6))
    orch_steady = Orchestrator(
        config=OrchestratorConfig(request_timeout_ms=12000.0, max_concurrency=4),
        bus=bus,
        standin_provider=standin_client,
    )
    await run_load_scenario(
        spec=ScenarioSpec(
            name="steady",
            source="stand-in",
            num_requests=18,
            concurrency_burst=1,
            inter_arrival_ms=1200.0,
            retrieval_setting="med_k5_rerank",
        ),
        orchestrator=orch_steady,
        pipeline=pipeline,
        bus=bus,
        questions=questions,
    )

    # Scenario B: Sudden Burst (high concurrency burst + client give-ups)
    print("  Running Scenario: burst...")
    standin_state.reset(StandInConfig(base_ttft_ms=150.0, load_slowdown_ms_per_inflight=40.0, max_concurrent=4))
    orch_burst = Orchestrator(
        config=OrchestratorConfig(request_timeout_ms=10000.0, max_concurrency=4),
        bus=bus,
        standin_provider=standin_client,
    )
    await run_load_scenario(
        spec=ScenarioSpec(
            name="burst",
            source="stand-in",
            num_requests=24,
            concurrency_burst=8,
            inter_arrival_ms=60.0,
            retrieval_setting="med_k5_rerank",
            patience_ms=5000.0,
            abandon_fraction=0.20,
            abandon_after_ms=350.0,
        ),
        orchestrator=orch_burst,
        pipeline=pipeline,
        bus=bus,
        questions=questions,
    )

    # Scenario C: Sustained Overload (load exceeds RPM limit)
    print("  Running Scenario: sustained_overload...")
    standin_state.reset(StandInConfig(base_ttft_ms=160.0, rpm_limit=25, tpm_limit=5000, max_concurrent=4))
    orch_overload = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=8000.0,
            max_concurrency=4,
            max_queue_size=12,
            base_backoff_ms=200.0,
            max_retries_per_call=2,
        ),
        bus=bus,
        standin_provider=standin_client,
    )
    await run_load_scenario(
        spec=ScenarioSpec(
            name="sustained_overload",
            source="stand-in",
            num_requests=30,
            concurrency_burst=6,
            inter_arrival_ms=40.0,
            retrieval_setting="med_k5_rerank",
            patience_ms=6000.0,
            abandon_fraction=0.15,
            abandon_after_ms=450.0,
        ),
        orchestrator=orch_overload,
        pipeline=pipeline,
        bus=bus,
        questions=questions,
    )

    # Scenario D: Per-Minute Limit Halved (RPM halved to 15, TPM halved to 3000)
    print("  Running Scenario: per_minute_limit_halved...")
    standin_state.reset(StandInConfig(base_ttft_ms=140.0, rpm_limit=15, tpm_limit=3000, max_concurrent=3))
    orch_half = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=7000.0,
            max_concurrency=3,
            max_queue_size=10,
            enable_adaptive_degradation=True,
            base_backoff_ms=200.0,
        ),
        bus=bus,
        standin_provider=standin_client,
    )
    await run_load_scenario(
        spec=ScenarioSpec(
            name="per_minute_limit_halved",
            source="stand-in",
            num_requests=20,
            concurrency_burst=4,
            inter_arrival_ms=80.0,
            retrieval_setting="med_k5_rerank",
        ),
        orchestrator=orch_half,
        pipeline=pipeline,
        bus=bus,
        questions=questions,
    )

    # Scenario E: Daily Budget Exhausted (3pm scenario: daily limit gone)
    print("  Running Scenario: daily_budget_exhausted...")
    # Configured on stand-in to test simulated provider-side 429 daily exhaustion rejection
    standin_state.reset(StandInConfig(daily_exhausted=True))
    orch_daily = Orchestrator(
        config=OrchestratorConfig(request_timeout_ms=5000.0),
        bus=bus,
        standin_provider=standin_client,
    )
    await run_load_scenario(
        spec=ScenarioSpec(
            name="daily_budget_exhausted",
            source="stand-in",
            num_requests=12,
            concurrency_burst=3,
            inter_arrival_ms=50.0,
            retrieval_setting="med_k5_rerank",
        ),
        orchestrator=orch_daily,
        pipeline=pipeline,
        bus=bus,
        questions=questions,
    )

    # -------------------------------------------------------------
    # Flush Raw CSV and JSONL
    # -------------------------------------------------------------
    csv_p, jsonl_p = bus.flush(output_dir)
    print(f"\n[Benchmark] Successfully flushed raw data:")
    print(f"  {csv_p.name} ({csv_p.stat().st_size} bytes, {len(bus.traces())} requests)")
    print(f"  {jsonl_p.name} ({jsonl_p.stat().st_size} bytes)")

    # -------------------------------------------------------------
    # Recompute Tables Deterministically from results.csv & traces
    # -------------------------------------------------------------
    rows = load_results_csv(csv_p)
    traces = load_traces_jsonl(jsonl_p)
    t2a, t2b = compute_scenario_tables(rows, traces=traces)
    t3 = compute_retrieval_table(rows)

    # Find one slow request for Heading 4
    slow_trace = None
    max_ms = 0.0
    for t in traces:
        tot = float(t.get("completion_offset_ms") or 0.0)
        if tot > max_ms and t.get("status") == "ok":
            max_ms = tot
            slow_trace = t
    slow_breakdown = analyze_slow_trace(slow_trace) if slow_trace else {}

    report_content = f"""# The Relay — Results and Evaluation Report

## Table 2a: Latency
{format_markdown_table(
    ["Scenario", "Real or stand-in", "Requests sent", "Duration (s)", "Offered rate (per min)", "First token p50 / p95 / p99 (ms)", "Total time p50 / p95 / p99 (ms)"],
    t2a
)}

## Table 2b: Outcomes
{format_markdown_table(
    ["Scenario", "Succeeded %", "Rate-limited %", "Timed out or given up %", "Retries per request", "Peak queue length", "Tokens per answer", "Cost per 1,000 answers"],
    t2b
)}

## Table 3: Retrieval Settings
{format_markdown_table(
    ["Setting", "Questions asked", "Correct and cited %", "Wrong %", "Correctly refused %", "Tokens per answer", "First token p50 / p95 (ms)", "Total time p50 / p95 (ms)"],
    t3
)}

## Heading 4: Where the Time Goes (Slow Request Trace Breakdown)
- **Request ID:** `{slow_breakdown.get('request_id')}`
- **Scenario:** `{slow_breakdown.get('scenario')}`
- **Status:** `{slow_breakdown.get('status')}`
- **Total Request Latency:** `{slow_breakdown.get('total_ms')} ms`
  - **Waiting (Queue Wait):** `{slow_breakdown.get('waiting_ms')} ms`
  - **Working (Pipeline Steps):** `{slow_breakdown.get('working_ms')} ms`
  - **Retrying (Backoff):** `{slow_breakdown.get('retrying_ms')} ms`
- **First Verified Token Delay:** `{slow_breakdown.get('before_first_token_ms')} ms` (delay occurred **before** the first verified token due to the Response Gate checking citations)
- **Post-First-Token Streaming:** `{slow_breakdown.get('after_first_token_ms')} ms`
- **Step Breakdown:**
"""
    for s in slow_breakdown.get("steps", []):
        report_content += f"  - `{s['step']}` (loop {s['loop']}): start={s['start_ms']}ms, duration={s['duration_ms']}ms\n"

    report_path = output_dir / "REPORT.md"
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\n[Benchmark] Complete Markdown report saved to {report_path.name}")
    print(report_content)


if __name__ == "__main__":
    asyncio.run(run_all())
