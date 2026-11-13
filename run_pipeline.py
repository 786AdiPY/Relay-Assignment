"""Interactive CLI to run questions against The Relay locally (via Groq or Stand-in)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from relay.config import DEFAULT_GROQ_MODEL, RUNS_DIR, get_groq_api_key
from relay.ingest.retriever import IndexRegistry
from relay.observability.bus import TelemetryBus
from relay.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from relay.pipeline.pipeline import AssistantPipeline
from relay.providers.circuit_breaker import CircuitBreaker
from relay.providers.groq import GroqProvider
from relay.providers.standin_client import StandInProvider
from relay.providers.standin_server import StandInConfig, create_standin_app


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="The Relay — Quota-aware paper Q&A pipeline")
    parser.add_argument("--question", "-q", type=str, default="", help="Question to ask")
    parser.add_argument("--source", "-s", choices=["real", "stand-in"], default="stand-in", help="Model source")
    parser.add_argument("--preset", "-p", default="med_k5_rerank", help="Retrieval preset (small_k3_norank, med_k5_rerank, large_k8_norank)")
    parser.add_argument("--timeout", type=float, default=18.0, help="End-to-end request deadline in seconds")
    parser.add_argument("--fallback", action="store_true", help="Allow stand-in fallback if Groq circuit breaker trips")
    args = parser.parse_args()

    q_text = args.question.strip()
    if not q_text:
        print("Enter a question about Attention Is All You Need, Moshi, or Dynamic Chunking:")
        try:
            q_text = input("> ").strip()
        except EOFError:
            return
        if not q_text:
            print("No question entered.")
            return

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    bus = TelemetryBus(output_dir=RUNS_DIR)

    standin_app, _ = create_standin_app(StandInConfig())
    standin_prov = StandInProvider(app=standin_app)

    groq_key = get_groq_api_key()
    groq_prov = GroqProvider(api_key=groq_key) if (args.source == "real" or args.fallback) else None

    cb = CircuitBreaker()
    orch = Orchestrator(
        config=OrchestratorConfig(
            request_timeout_ms=args.timeout * 1000.0,
            allow_standin_fallback=args.fallback,
        ),
        bus=bus,
        groq_provider=groq_prov,
        standin_provider=standin_prov,
        circuit_breaker=cb,
    )

    registry = IndexRegistry()
    pipeline = AssistantPipeline(registry=registry, bus=bus)

    print(f"\n[Relay] Routing question ({args.source}, preset={args.preset}, deadline={args.timeout}s)...")
    res = await orch.submit_request(
        request_id="cli_req_01",
        question=q_text,
        scenario="interactive_cli",
        source=args.source,
        retrieval_setting=args.preset,
        pipeline_fn=lambda ctx, cm, rset, ml: pipeline.run(ctx, q_text, cm, rset, ml),
    )

    csv_path, jsonl_path = bus.flush(RUNS_DIR, append=True)
    print("\n--- Answer ---")
    print(res.get("answer", ""))
    citations = res.get("citations") or []
    if citations:
        print("\nCitations:", ", ".join(citations))

    trace = bus.traces()[0]
    print("\n--- Latency & Token Breakdown ---")
    print(f"Status:             {trace.status}")
    print(f"Verified First Tok: {trace.first_verified_token_offset_ms or 0:.1f} ms")
    print(f"Total Latency:      {trace.completion_offset_ms or 0:.1f} ms")
    print(f"Tokens In/Out:      {trace.tokens_in} / {trace.tokens_out}")
    print(f"Retries:            {trace.retries}")
    print(f"Steps Executed:     {[s.step for s in trace.steps]}")
    print(f"\nLogged to: {csv_path.name} and {jsonl_path.name}")


if __name__ == "__main__":
    asyncio.run(main_async())
