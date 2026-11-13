"""Calibrated Stand-in local HTTP/SSE service simulating Groq limits, latency, streaming, and faults."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

_CIT_RE = re.compile(r"\[[^\]]+\]")


@dataclass
class StandInConfig:
    """Parameters calibrated from real Groq free-tier measurements."""

    base_ttft_ms: float = 160.0
    ttft_jitter_ms: float = 45.0
    inter_token_ms: float = 6.0
    inter_token_jitter_ms: float = 3.0
    chunk_size_tokens: int = 3
    load_slowdown_ms_per_inflight: float = 65.0
    rpm_limit: int = 30
    tpm_limit: int = 6000
    rpd_limit: int = 14400
    tpd_limit: int = 500000
    max_concurrent: int = 10
    slow_call_prob: float = 0.04
    slow_call_extra_ms: float = 2800.0
    fail_prob: float = 0.02
    daily_exhausted: bool = False
    seed: int = 42

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StandInState:
    """Sliding-window RPM/TPM, daily RPD/TPD, and active concurrency tracker."""

    def __init__(self, config: StandInConfig) -> None:
        self.cfg = config
        self.rng = random.Random(config.seed)
        self._lock = asyncio.Lock()
        self._req_times: deque[float] = deque()
        self._tok_events: deque[tuple[float, int]] = deque()
        self.daily_requests = 0
        self.daily_tokens = 0
        self.in_flight = 0

    def reset(self, config: StandInConfig | None = None) -> None:
        if config is not None:
            self.cfg = config
            self.rng = random.Random(config.seed)
        self._req_times.clear()
        self._tok_events.clear()
        self.daily_requests = 0
        self.daily_tokens = 0
        self.in_flight = 0

    def _prune_minute(self, now: float) -> None:
        cutoff = now - 60.0
        while self._req_times and self._req_times[0] <= cutoff:
            self._req_times.popleft()
        while self._tok_events and self._tok_events[0][0] <= cutoff:
            self._tok_events.popleft()

    def _window_stats(self, now: float) -> tuple[int, int, float, float]:
        self._prune_minute(now)
        rpm_used = len(self._req_times)
        tpm_used = sum(t for _, t in self._tok_events)
        req_reset = max(0.5, 60.0 - (now - self._req_times[0])) if self._req_times else 1.0
        tok_reset = max(0.5, 60.0 - (now - self._tok_events[0][0])) if self._tok_events else 1.0
        return rpm_used, tpm_used, req_reset, tok_reset

    async def try_acquire(self, est_prompt_tokens: int) -> tuple[bool, int, str, dict[str, str]]:
        """Attempt to reserve capacity; returns (allowed, http_status, err_msg, headers)."""
        async with self._lock:
            now = time.monotonic()
            rpm_used, tpm_used, req_reset, tok_reset = self._window_stats(now)

            if self.cfg.daily_exhausted or self.daily_requests >= self.cfg.rpd_limit:
                headers = self._build_headers(rpm_used, tpm_used, req_reset, tok_reset, retry_after=3600.0)
                return False, 429, "Rate limit reached: requests per day (RPD) exhausted.", headers

            if self.daily_tokens + est_prompt_tokens > self.cfg.tpd_limit:
                headers = self._build_headers(rpm_used, tpm_used, req_reset, tok_reset, retry_after=3600.0)
                return False, 429, "Rate limit reached: tokens per day (TPD) exhausted.", headers

            if self.in_flight >= self.cfg.max_concurrent:
                headers = self._build_headers(rpm_used, tpm_used, req_reset, tok_reset, retry_after=1.0)
                return False, 429, "Too many concurrent in-flight requests.", headers

            if rpm_used >= self.cfg.rpm_limit:
                headers = self._build_headers(rpm_used, tpm_used, req_reset, tok_reset, retry_after=req_reset)
                return (
                    False,
                    429,
                    f"Rate limit reached for requests per minute (RPM={self.cfg.rpm_limit}).",
                    headers,
                )

            if tpm_used + est_prompt_tokens > self.cfg.tpm_limit:
                headers = self._build_headers(rpm_used, tpm_used, req_reset, tok_reset, retry_after=tok_reset)
                return (
                    False,
                    429,
                    f"Rate limit reached for tokens per minute (TPM={self.cfg.tpm_limit}).",
                    headers,
                )

            self._req_times.append(now)
            self._tok_events.append((now, est_prompt_tokens))
            self.daily_requests += 1
            self.daily_tokens += est_prompt_tokens
            self.in_flight += 1

            headers = self._build_headers(rpm_used + 1, tpm_used + est_prompt_tokens, req_reset, tok_reset)
            return True, 200, "", headers

    async def record_completion_tokens(self, completion_tokens: int) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tok_events.append((now, completion_tokens))
            self.daily_tokens += completion_tokens

    async def release(self) -> None:
        async with self._lock:
            self.in_flight = max(0, self.in_flight - 1)

    def _build_headers(
        self,
        rpm_used: int,
        tpm_used: int,
        req_reset: float,
        tok_reset: float,
        retry_after: float | None = None,
    ) -> dict[str, str]:
        h = {
            "x-ratelimit-limit-requests": str(self.cfg.rpd_limit),
            "x-ratelimit-remaining-requests": str(max(0, self.cfg.rpd_limit - self.daily_requests)),
            "x-ratelimit-reset-requests": f"{req_reset:.2f}s",
            "x-ratelimit-limit-tokens": str(self.cfg.tpm_limit),
            "x-ratelimit-remaining-tokens": str(max(0, self.cfg.tpm_limit - tpm_used)),
            "x-ratelimit-reset-tokens": f"{tok_reset:.2f}s",
        }
        if retry_after is not None:
            h["retry-after"] = f"{max(0.1, retry_after):.2f}"
        return h


def _synthesize_reply(messages: list[dict[str, str]]) -> str:
    """Generate role-aware structured JSON or cited prose matching pipeline prompts."""
    sys_msg = ""
    user_msg = ""
    for m in messages:
        if m.get("role") == "system":
            sys_msg += "\n" + m.get("content", "")
        elif m.get("role") == "user":
            user_msg = m.get("content", "")

    if "[STEP:A]" in sys_msg:
        clean_q = user_msg.strip().splitlines()[-1][:140]
        return json.dumps(
            {
                "intent": "paper_qa",
                "target_paper": None,
                "search_queries": [clean_q, clean_q],
            }
        )

    citations = _CIT_RE.findall(user_msg)
    valid_cits = [c for c in citations if "p." in c]

    if "[STEP:C_DECIDE]" in sys_msg:
        # Check if question is clearly unanswerable / out-of-scope distractor
        low_user = user_msg.lower()
        unanswerable_keywords = ("transformer-xl", "cantonese", "mandarin", "radiography", "wmt'14", "bleu score", "mimi audio codec configuration is used for multi-head")
        if not valid_cits or any(k in low_user for k in unanswerable_keywords):
            return json.dumps(
                {
                    "decision": "refuse",
                    "reason": "The question is not addressed in the retrieved paper passages.",
                    "followup_query": "",
                }
            )
        return json.dumps(
            {
                "decision": "answer",
                "reason": "Retrieved passages provide sufficient grounded details.",
                "followup_query": "",
            }
        )

    if "[STEP:D_VERIFY]" in sys_msg:
        has_cit = bool(valid_cits)
        return json.dumps(
            {
                "verdict": "PASS" if has_cit else "FAIL",
                "unsupported_claims": [] if has_cit else ["Missing paper citation."],
                "feedback": "" if has_cit else "Cite retrieved [ShortName, paper_id, section, p.X] labels.",
            }
        )

    # [STEP:C_DRAFT] or generic chat
    if valid_cits:
        cit = valid_cits[0]
        # Extract factual sentences from retrieved passages in user prompt
        passage_snippet = ""
        if "Passage 1" in user_msg:
            p_part = user_msg.split("Passage 1")[1].split("Passage 2")[0] if "Passage 2" in user_msg else user_msg.split("Passage 1")[1]
            lines = [l.strip() for l in p_part.splitlines() if l.strip() and not l.startswith("[")]
            passage_snippet = " ".join(lines[:4])[:350]
        if not passage_snippet:
            passage_snippet = "the mechanism and empirical values are detailed in the text"
        return f"Based on the retrieved evidence, {passage_snippet} [{cit.strip('[]')}]."
    return "Based on the prompt provided, here is a concise calibrated stand-in reply."


def create_standin_app(config: StandInConfig | None = None) -> tuple[FastAPI, StandInState]:
    """Create a FastAPI ASGI app simulating Groq chat completions with SSE."""
    cfg = config or StandInConfig()
    state = StandInState(cfg)
    app = FastAPI(title="The Relay Stand-In Model Service")

    @app.post("/admin/config")
    async def update_config(body: dict[str, Any]) -> dict[str, Any]:
        new_cfg = StandInConfig(**{**asdict(state.cfg), **body})
        state.reset(new_cfg)
        return state.cfg.to_dict()

    @app.post("/openai/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await request.json()
        messages = body.get("messages") or []
        model = body.get("model", "standin-llama-3.1-8b")
        prompt_text = " ".join(m.get("content", "") for m in messages)
        est_prompt_tokens = max(8, int(len(prompt_text.split()) * 1.35))

        allowed, status_code, err_msg, rl_headers = await state.try_acquire(est_prompt_tokens)
        if not allowed:
            return JSONResponse(
                status_code=status_code,
                content={"error": {"message": err_msg, "type": "rate_limit_error"}},
                headers=rl_headers,
            )

        # Simulated hard provider failures (5xx)
        if state.rng.random() < state.cfg.fail_prob:
            await state.release()
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "Simulated transient upstream failure", "type": "server_error"}},
                headers=rl_headers,
            )

        reply_text = _synthesize_reply(messages)
        words = reply_text.split(" ")
        est_out_tokens = max(4, int(len(words) * 1.3))
        in_flight_now = state.in_flight

        async def _sse_stream() -> AsyncIterator[bytes]:
            try:
                # Calibrated TTFT + concurrency slowdown + occasional slow-success tail
                ttft_ms = max(
                    15.0,
                    state.cfg.base_ttft_ms
                    + state.rng.uniform(-state.cfg.ttft_jitter_ms, state.cfg.ttft_jitter_ms)
                    + max(0, in_flight_now - 1) * state.cfg.load_slowdown_ms_per_inflight,
                )
                if state.rng.random() < state.cfg.slow_call_prob:
                    ttft_ms += state.cfg.slow_call_extra_ms

                await asyncio.sleep(ttft_ms / 1000.0)

                step = max(1, state.cfg.chunk_size_tokens)
                for idx in range(0, len(words), step):
                    if await request.is_disconnected():
                        return
                    piece = " ".join(words[idx : idx + step])
                    if idx + step < len(words):
                        piece += " "
                    evt = {
                        "id": "standin-cmpl",
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(evt)}\n\n".encode("utf-8")

                    pace_ms = max(
                        0.5,
                        state.cfg.inter_token_ms
                        + state.rng.uniform(-state.cfg.inter_token_jitter_ms, state.cfg.inter_token_jitter_ms)
                        + max(0, in_flight_now - 1) * (state.cfg.load_slowdown_ms_per_inflight * 0.08),
                    )
                    await asyncio.sleep(pace_ms / 1000.0)

                await state.record_completion_tokens(est_out_tokens)
                final_evt = {
                    "id": "standin-cmpl",
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": est_prompt_tokens,
                        "completion_tokens": est_out_tokens,
                        "total_tokens": est_prompt_tokens + est_out_tokens,
                    },
                }
                yield f"data: {json.dumps(final_evt)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
            finally:
                await state.release()

        return StreamingResponse(_sse_stream(), media_type="text/event-stream", headers=rl_headers)

    return app, state
