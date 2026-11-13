"""Deterministic recomputation of Tables 2a, 2b, 3, and Heading 4 breakdown from raw files."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from relay.config import DEFAULT_GROQ_MODEL, GROQ_PRICING, RETRIEVAL_PRESETS


def _parse_iso_ms(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    return float(np.percentile(np.asarray(vals, dtype=float), q))


def _fmt_ms(val: float | None) -> str:
    return f"{val:.0f}" if val is not None else "n/a"


def load_results_csv(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_traces_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def estimate_peak_concurrency_from_csv(rows: list[dict[str, str]], max_concurrency: int) -> int:
    """Recompute peak queue depth purely from CSV start_time and total_ms intervals."""
    events: list[tuple[float, int]] = []
    for r in rows:
        if not r.get("start_time"):
            continue
        start = _parse_iso_ms(r["start_time"])
        dur = float(r.get("total_ms") or 0.0)
        events.append((start, 1))
        events.append((start + max(0.1, dur), -1))
    events.sort(key=lambda x: (x[0], x[1]))
    active = 0
    peak_active = 0
    for _, delta in events:
        active += delta
        if active > peak_active:
            peak_active = active
    return max(0, peak_active - max_concurrency)


def compute_scenario_tables(
    rows: list[dict[str, str]],
    *,
    model_id: str = DEFAULT_GROQ_MODEL,
    max_concurrency: int = 4,
    traces: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute Table 2a (Latency) and Table 2b (Outcomes) grouped by scenario."""
    by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        if r["scenario"].startswith("retrieval_"):
            continue
        by_scenario[r["scenario"]].append(r)

    trace_peak_q: dict[str, int] = defaultdict(int)
    if traces:
        for t in traces:
            sc = t.get("scenario", "")
            qd = int(t.get("queue_depth_at_enter") or 0)
            if qd > trace_peak_q[sc]:
                trace_peak_q[sc] = qd

    pricing = GROQ_PRICING.get(model_id, GROQ_PRICING["llama-3.1-8b-instant"])
    table2a: list[dict[str, Any]] = []
    table2b: list[dict[str, Any]] = []

    for scenario, group in by_scenario.items():
        n = len(group)
        sources = sorted({r["source"] for r in group})
        source_str = sources[0] if len(sources) == 1 else "/".join(sources)

        starts = [_parse_iso_ms(r["start_time"]) for r in group]
        ends = [_parse_iso_ms(r["start_time"]) + float(r["total_ms"] or 0.0) for r in group]
        duration_s = max(0.01, (max(ends) - min(starts)) / 1000.0)
        arrival_span_s = max(1.0, (max(starts) - min(starts)) / 1000.0) if n > 1 else duration_s
        offered_rpm = (n / arrival_span_s) * 60.0 if max(starts) > min(starts) else (n / duration_s) * 60.0

        ft_vals = [float(r["first_token_ms"]) for r in group if r.get("first_token_ms", "") != ""]
        tot_vals = [float(r["total_ms"]) for r in group]

        table2a.append(
            {
                "Scenario": scenario,
                "Real or stand-in": source_str,
                "Requests sent": n,
                "Duration (s)": round(duration_s, 1),
                "Offered rate (per min)": round(offered_rpm, 1),
                "First token p50 / p95 / p99 (ms)": (
                    f"{_fmt_ms(_pct(ft_vals, 50))} / {_fmt_ms(_pct(ft_vals, 95))} / {_fmt_ms(_pct(ft_vals, 99))}"
                ),
                "Total time p50 / p95 / p99 (ms)": (
                    f"{_fmt_ms(_pct(tot_vals, 50))} / {_fmt_ms(_pct(tot_vals, 95))} / {_fmt_ms(_pct(tot_vals, 99))}"
                ),
            }
        )

        ok_rows = [r for r in group if r["status"] == "ok"]
        rl_rows = [r for r in group if r["status"] == "rate_limited"]
        to_rows = [r for r in group if r["status"] == "timeout"]
        total_retries = sum(int(r["retries"] or 0) for r in group)

        tok_basis = ok_rows if ok_rows else group
        avg_in = sum(int(r["tokens_in"] or 0) for r in tok_basis) / len(tok_basis)
        avg_out = sum(int(r["tokens_out"] or 0) for r in tok_basis) / len(tok_basis)
        tokens_per_answer = avg_in + avg_out
        cost_per_1k = (
            (avg_in / 1_000_000.0) * pricing.input_per_1m_usd
            + (avg_out / 1_000_000.0) * pricing.output_per_1m_usd
        ) * 1000.0

        peak_q = max(
            estimate_peak_concurrency_from_csv(group, max_concurrency),
            trace_peak_q.get(scenario, 0),
        )

        table2b.append(
            {
                "Scenario": scenario,
                "Succeeded %": round(len(ok_rows) / n * 100.0, 1),
                "Rate-limited %": round(len(rl_rows) / n * 100.0, 1),
                "Timed out or given up %": round(len(to_rows) / n * 100.0, 1),
                "Retries per request": round(total_retries / n, 2),
                "Peak queue length": peak_q,
                "Tokens per answer": round(tokens_per_answer, 1),
                "Cost per 1,000 answers": f"${cost_per_1k:.4f}",
            }
        )

    return table2a, table2b


def compute_retrieval_table(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Compute Table 3 (Retrieval settings) grouped by retrieval_setting."""
    by_setting: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        if r.get("answer_grade", "") != "" or r["scenario"].startswith("retrieval_"):
            by_setting[r["retrieval_setting"]].append(r)

    table3: list[dict[str, Any]] = []
    for setting, group in by_setting.items():
        n = len(group)
        correct = sum(1 for r in group if r["answer_grade"] == "correct")
        wrong = sum(1 for r in group if r["answer_grade"] in ("wrong", "refused_wrongly"))
        refused_ok = sum(1 for r in group if r["answer_grade"] == "refused_correctly")

        ok_rows = [r for r in group if r["status"] == "ok"]
        tok_basis = ok_rows if ok_rows else group
        avg_tokens = sum(int(r["tokens_in"] or 0) + int(r["tokens_out"] or 0) for r in tok_basis) / len(tok_basis)

        ft_vals = [float(r["first_token_ms"]) for r in group if r.get("first_token_ms", "") != ""]
        tot_vals = [float(r["total_ms"]) for r in group]

        preset = RETRIEVAL_PRESETS.get(setting)
        label = (
            f"{setting} ({preset.chunk_tokens} tok, k={preset.top_k}, rerank={'yes' if preset.rerank else 'no'})"
            if preset
            else setting
        )
        table3.append(
            {
                "Setting": label,
                "Questions asked": n,
                "Correct and cited %": round(correct / n * 100.0, 1),
                "Wrong %": round(wrong / n * 100.0, 1),
                "Correctly refused %": round(refused_ok / n * 100.0, 1),
                "Tokens per answer": round(avg_tokens, 1),
                "First token p50 / p95 (ms)": f"{_fmt_ms(_pct(ft_vals, 50))} / {_fmt_ms(_pct(ft_vals, 95))}",
                "Total time p50 / p95 (ms)": f"{_fmt_ms(_pct(tot_vals, 50))} / {_fmt_ms(_pct(tot_vals, 95))}",
            }
        )
    return table3


def analyze_slow_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Split a trace into waiting, working, retrying, and pre- vs post-first-token latency."""
    queue_wait = float(trace.get("queue_wait_ms") or 0.0)
    retry_ms = sum(float(r.get("backoff_ms") or 0.0) for r in trace.get("retry_events", []))
    work_ms = sum(float(s.get("duration_ms") or 0.0) for s in trace.get("steps", []))
    total_ms = float(trace.get("completion_offset_ms") or 0.0)
    ft = trace.get("first_verified_token_offset_ms")
    pre_ft = float(ft) if ft is not None else total_ms
    post_ft = max(0.0, total_ms - pre_ft) if ft is not None else 0.0

    return {
        "request_id": trace.get("request_id"),
        "scenario": trace.get("scenario"),
        "status": trace.get("status"),
        "total_ms": round(total_ms, 2),
        "waiting_ms": round(queue_wait, 2),
        "working_ms": round(work_ms, 2),
        "retrying_ms": round(retry_ms, 2),
        "before_first_token_ms": round(pre_ft, 2),
        "after_first_token_ms": round(post_ft, 2),
        "steps": [
            {
                "step": s["step"],
                "loop": s["loop_idx"],
                "start_ms": round(s["start_offset_ms"], 1),
                "end_ms": round(s["end_offset_ms"], 1),
                "duration_ms": round(s["duration_ms"], 1),
            }
            for s in trace.get("steps", [])
        ],
    }
