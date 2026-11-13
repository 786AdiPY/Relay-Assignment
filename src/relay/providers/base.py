"""Shared streaming ModelProvider interface, rate-limit structures, and duration parsing."""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx


def parse_duration_seconds(raw: str | None) -> float | None:
    """Parse Groq duration header strings such as '14s', '2m30.5s', '120ms', or '1.2'."""
    if not raw:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass

    total = 0.0
    matched = False
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", s):
        matched = True
        num = float(val)
        if unit == "ms":
            total += num / 1000.0
        elif unit == "s":
            total += num
        elif unit == "m":
            total += num * 60.0
        elif unit == "h":
            total += num * 3600.0
    return total if matched else None


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass
class RateLimitHeaders:
    """Parsed Groq / OpenAI-compatible rate-limit headers."""

    limit_requests: int | None = None
    remaining_requests: int | None = None
    reset_requests_s: float | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    retry_after_s: float | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> RateLimitHeaders:
        lower = {k.lower(): v for k, v in headers.items()}

        def _int(key: str) -> int | None:
            v = lower.get(key)
            if v is None:
                return None
            try:
                return int(float(v))
            except ValueError:
                return None

        relevant = {
            k: v
            for k, v in lower.items()
            if k.startswith("x-ratelimit") or k == "retry-after"
        }
        return cls(
            limit_requests=_int("x-ratelimit-limit-requests"),
            remaining_requests=_int("x-ratelimit-remaining-requests"),
            reset_requests_s=parse_duration_seconds(lower.get("x-ratelimit-reset-requests")),
            limit_tokens=_int("x-ratelimit-limit-tokens"),
            remaining_tokens=_int("x-ratelimit-remaining-tokens"),
            reset_tokens_s=parse_duration_seconds(lower.get("x-ratelimit-reset-tokens")),
            retry_after_s=parse_duration_seconds(lower.get("retry-after")),
            raw=relevant,
        )


@dataclass
class StreamChunk:
    """Incremental chunk yielded by ModelProvider.stream_chat."""

    delta: str = ""
    is_final: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    status_code: int = 200
    rate_limit: RateLimitHeaders | None = None


@dataclass
class ProviderResponse:
    """Complete aggregated response from one ModelProvider call."""

    text: str
    tokens_in: int
    tokens_out: int
    provider_ttft_ms: float | None
    total_ms: float
    status_code: int
    model: str
    provider_name: str
    rate_limit: RateLimitHeaders = field(default_factory=RateLimitHeaders)


class ProviderError(Exception):
    """Structured error raised by ModelProvider implementations."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        is_rate_limit: bool = False,
        is_daily_limit: bool = False,
        is_timeout: bool = False,
        retry_after_s: float | None = None,
        rate_limit: RateLimitHeaders | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.is_rate_limit = is_rate_limit
        self.is_daily_limit = is_daily_limit
        self.is_timeout = is_timeout
        self.retry_after_s = retry_after_s
        self.rate_limit = rate_limit or RateLimitHeaders()


class ModelProvider(ABC):
    """Shared async streaming interface for Groq and the calibrated Stand-in."""

    name: str
    source_label: str

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout_s: float = 15.0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Yield StreamChunk items as tokens arrive, closing immediately on cancel/timeout."""

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout_s: float = 15.0,
        cancel_event: asyncio.Event | None = None,
    ) -> ProviderResponse:
        """Consume stream_chat and return complete text, usage, and TTFT telemetry."""
        import time

        t0 = time.perf_counter()
        parts: list[str] = []
        ttft_ms: float | None = None
        tokens_in = 0
        tokens_out = 0
        status_code = 200
        rate_limit = RateLimitHeaders()

        async for chunk in self.stream_chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            cancel_event=cancel_event,
        ):
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Request cancelled by client or deadline")
            if chunk.delta:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t0) * 1000.0
                parts.append(chunk.delta)
            if chunk.rate_limit is not None:
                rate_limit = chunk.rate_limit
            if chunk.is_final:
                tokens_in = chunk.tokens_in
                tokens_out = chunk.tokens_out
                status_code = chunk.status_code

        total_ms = (time.perf_counter() - t0) * 1000.0
        text = "".join(parts)
        if tokens_out == 0 and text:
            tokens_out = max(1, len(text.split()))
        return ProviderResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider_ttft_ms=ttft_ms,
            total_ms=total_ms,
            status_code=status_code,
            model=model,
            provider_name=self.name,
            rate_limit=rate_limit,
        )


def classify_daily_limit(body_text: str, headers: RateLimitHeaders) -> bool:
    """Distinguish daily quota exhaustion (RPD/TPD) from per-minute (RPM/TPM) limits."""
    low = body_text.lower()
    if any(k in low for k in ("per day", "rpd", "tpd", "daily")):
        return True
    if headers.reset_requests_s is not None and headers.reset_requests_s > 120.0:
        if headers.remaining_requests == 0:
            return True
    if headers.reset_tokens_s is not None and headers.reset_tokens_s > 120.0:
        if headers.remaining_tokens == 0:
            return True
    return False


async def stream_openai_sse(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    payload: dict,
    timeout_s: float,
    provider_label: str,
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[StreamChunk]:
    """Shared OpenAI-compatible SSE streaming helper with cancellation and header parsing."""
    timeout = httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))
    try:
        async with client.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
            rl = RateLimitHeaders.from_headers(dict(resp.headers))
            if resp.status_code != 200:
                raw_body = (await resp.aread()).decode("utf-8", errors="replace")
                is_rl = resp.status_code == 429
                is_daily = is_rl and classify_daily_limit(raw_body, rl)
                raise ProviderError(
                    f"{provider_label} HTTP {resp.status_code}: {raw_body[:300]}",
                    status_code=resp.status_code,
                    is_rate_limit=is_rl,
                    is_daily_limit=is_daily,
                    retry_after_s=rl.retry_after_s,
                    rate_limit=rl,
                )

            tokens_in = 0
            tokens_out = 0
            async for line in resp.aiter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    await resp.aclose()
                    raise asyncio.CancelledError(f"Cancelled during {provider_label} SSE stream")
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                usage = event.get("usage") or (event.get("x_groq") or {}).get("usage")
                if isinstance(usage, dict):
                    tokens_in = int(usage.get("prompt_tokens", tokens_in) or tokens_in)
                    tokens_out = int(usage.get("completion_tokens", tokens_out) or tokens_out)

                choices = event.get("choices") or []
                delta_text = ""
                if choices:
                    delta = choices[0].get("delta") or {}
                    delta_text = delta.get("content") or delta.get("reasoning") or ""
                if delta_text:
                    yield StreamChunk(delta=delta_text, status_code=200, rate_limit=rl)

            yield StreamChunk(
                delta="",
                is_final=True,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                status_code=200,
                rate_limit=rl,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            f"{provider_label} request timed out after {timeout_s:.2f}s",
            status_code=504,
            is_timeout=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider_label} HTTP error: {exc}", status_code=503) from exc
