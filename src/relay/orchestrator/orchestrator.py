"""Layer 2 Orchestrator: validation, queue, concurrency cap, retries, and graceful shutdown."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from relay.config import DEFAULT_GROQ_MODEL
from relay.observability.bus import TelemetryBus, mono_ms
from relay.observability.models import ProviderCallRecord, SourceType, StatusType
from relay.orchestrator.context import (
    DeadlineExceededError,
    RequestCancelledError,
    RequestContext,
)
from relay.orchestrator.quota import QuotaTracker, choose_adaptive_retrieval
from relay.providers.base import ChatMessage, ModelProvider, ProviderError, ProviderResponse
from relay.providers.circuit_breaker import CircuitBreaker

ModelCallFn = Callable[[str, list[ChatMessage], int, float], Awaitable[ProviderResponse]]
PipelineRunnerFn = Callable[[RequestContext, ModelCallFn, str, int], Awaitable[dict[str, Any]]]


@dataclass
class OrchestratorConfig:
    request_timeout_ms: float = 18000.0
    per_call_timeout_ms: float = 7000.0
    max_queue_size: int = 80
    max_concurrency: int = 4
    max_retries_per_call: int = 2
    base_backoff_ms: float = 250.0
    max_backoff_ms: float = 3500.0
    honor_retry_after: bool = True
    jitter: bool = True
    enable_adaptive_degradation: bool = True
    allow_standin_fallback: bool = False
    model_name: str = DEFAULT_GROQ_MODEL


class Orchestrator:
    """Coordinates queueing, shared deadlines, circuit breaking, retries, and cancellation."""

    def __init__(
        self,
        *,
        config: OrchestratorConfig,
        bus: TelemetryBus,
        groq_provider: ModelProvider | None = None,
        standin_provider: ModelProvider | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        quota_tracker: QuotaTracker | None = None,
    ) -> None:
        self.cfg = config
        self.bus = bus
        self.groq_provider = groq_provider
        self.standin_provider = standin_provider
        self.breaker = circuit_breaker or CircuitBreaker()
        self.quota = quota_tracker or QuotaTracker()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._queued_count = 0
        self._in_flight_count = 0
        self._active_contexts: dict[str, RequestContext] = {}
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False
        self._rng = random.Random(101)

    def cancel_request(self, request_id: str, reason: str = "client_abandoned") -> bool:
        """Signal an in-flight or queued request to abort immediately."""
        ctx = self._active_contexts.get(request_id)
        if ctx is None:
            return False
        ctx.cancel(reason)
        self.bus.record_cancellation(request_id, reason)
        return True

    async def shutdown(self, grace_s: float = 2.0) -> None:
        """Gracefully stop admitting requests, cancel pending work, and flush telemetry."""
        self._shutting_down = True
        for rid, ctx in list(self._active_contexts.items()):
            ctx.cancel("shutdown")
            self.bus.record_cancellation(rid, "shutdown")
        if self._active_tasks:
            _, pending = await asyncio.wait(self._active_tasks, timeout=grace_s)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def submit_request(
        self,
        *,
        request_id: str,
        question: str,
        scenario: str,
        source: SourceType,
        retrieval_setting: str,
        pipeline_fn: PipelineRunnerFn,
        question_id: str = "",
        patience_ms: float | None = None,
        allow_standin_fallback: bool | None = None,
    ) -> dict[str, Any]:
        """Validate, queue, enforce deadline, execute pipeline, and emit outcome."""
        deadline_budget_ms = min(
            self.cfg.request_timeout_ms,
            patience_ms if patience_ms is not None else self.cfg.request_timeout_ms,
        )
        trace = self.bus.record_arrival(
            request_id=request_id,
            scenario=scenario,
            source=source,
            deadline_ms=deadline_budget_ms,
            retrieval_setting=retrieval_setting,
            question_id=question_id,
        )
        arrival = trace.arrival_mono_ms
        ctx = RequestContext(
            request_id=request_id,
            scenario=scenario,
            source=source,
            question_id=question_id,
            retrieval_setting=retrieval_setting,
            arrival_mono_ms=arrival,
            deadline_mono_ms=arrival + deadline_budget_ms,
            allow_standin_fallback=(
                allow_standin_fallback
                if allow_standin_fallback is not None
                else self.cfg.allow_standin_fallback
            ),
        )

        # Validation
        if not question or not question.strip():
            self.bus.record_completion(request_id, status="error", error_detail="Empty question")
            return {"status": "error", "answer": "Please enter a non-empty question."}

        if self._shutting_down:
            self.bus.record_completion(request_id, status="error", error_detail="Service shutting down")
            return {"status": "error", "answer": "Service is shutting down. Please retry shortly."}

        # Daily budget exhausted fast-fail (3pm scenario / real daily limit hit)
        if source == "real" and not ctx.allow_standin_fallback and self.quota.pressure_level() == "daily_exhausted":
            msg = (
                "The daily model quota has been exhausted for today. "
                "No grounded reply can be generated right now; please try again after the daily reset."
            )
            self.bus.record_first_verified_token(request_id)
            self.bus.record_completion(
                request_id,
                status="rate_limited",
                answer_text=msg,
                error_detail="daily_budget_exhausted_fail_fast",
                circuit_state=self.breaker.state,
            )
            return {"status": "rate_limited", "answer": msg, "citations": []}

        # Queue capacity check
        if self._queued_count >= self.cfg.max_queue_size:
            msg = "The assistant is temporarily overloaded (queue full). Please try again shortly."
            self.bus.record_completion(request_id, status="rate_limited", error_detail="queue_full")
            return {"status": "rate_limited", "answer": msg, "citations": []}

        self._active_contexts[request_id] = ctx
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_tasks.add(current_task)

        acquired = False
        queued = False
        try:
            self._queued_count += 1
            queued = True
            self.bus.record_queue_enter(request_id, self._queued_count)

            try:
                acquired = await self._acquire_slot_with_deadline(ctx)
            finally:
                if queued:
                    self._queued_count = max(0, self._queued_count - 1)
                    queued = False

            if not acquired:
                reason = ctx.cancel_reason or "queue_deadline_exceeded"
                self.bus.record_cancellation(request_id, reason)
                self.bus.record_completion(request_id, status="timeout", error_detail=reason)
                return {"status": "timeout", "answer": "Request timed out while waiting in queue."}

            self._in_flight_count += 1
            self.bus.record_queue_exit(request_id, self._in_flight_count)

            # Adaptive degradation when per-minute limit is near
            effective_setting = retrieval_setting
            max_loops = 2
            if self.cfg.enable_adaptive_degradation:
                pressure = self.quota.pressure_level()
                effective_setting, max_loops, mode = choose_adaptive_retrieval(retrieval_setting, pressure)
                if mode != "none":
                    self.bus.record_degradation(request_id, mode, effective_setting)

            async def call_model(
                step_name: str,
                messages: list[ChatMessage],
                max_tokens: int = 450,
                temperature: float = 0.0,
            ) -> ProviderResponse:
                return await self._invoke_with_retry(
                    ctx,
                    step_name=step_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

            result = await pipeline_fn(ctx, call_model, effective_setting, max_loops)
            status: StatusType = result.get("status", "ok")
            self.bus.record_completion(
                request_id,
                status=status,
                answer_text=result.get("answer", ""),
                citations=result.get("citations", []),
                circuit_state=self.breaker.state,
            )
            return result

        except (RequestCancelledError, asyncio.CancelledError) as exc:
            reason = ctx.cancel_reason or "client_abandoned"
            self.bus.record_cancellation(request_id, reason)
            self.bus.record_completion(
                request_id, status="timeout", error_detail=f"cancelled:{reason}", circuit_state=self.breaker.state
            )
            return {"status": "timeout", "answer": "Request cancelled."}
        except DeadlineExceededError as exc:
            self.bus.record_completion(
                request_id, status="timeout", error_detail=str(exc), circuit_state=self.breaker.state
            )
            return {"status": "timeout", "answer": "Request exceeded its end-to-end deadline."}
        except ProviderError as exc:
            status_val: StatusType = "rate_limited" if exc.is_rate_limit else ("timeout" if exc.is_timeout else "error")
            msg = (
                "The daily model quota has been exhausted for today. Please try again after quota reset."
                if exc.is_daily_limit
                else (
                    "Rate limit reached on model provider; please retry shortly."
                    if exc.is_rate_limit
                    else "Model provider error occurred before completing your reply."
                )
            )
            self.bus.record_completion(
                request_id,
                status=status_val,
                answer_text=msg if exc.is_daily_limit else "",
                error_detail=exc.message,
                circuit_state=self.breaker.state,
            )
            return {"status": status_val, "answer": msg, "citations": []}
        except Exception as exc:
            self.bus.record_completion(request_id, status="error", error_detail=repr(exc))
            return {"status": "error", "answer": "Internal error processing request."}
        finally:
            if acquired:
                self._in_flight_count = max(0, self._in_flight_count - 1)
                self._semaphore.release()
            self._active_contexts.pop(request_id, None)
            if current_task is not None:
                self._active_tasks.discard(current_task)

    async def _acquire_slot_with_deadline(self, ctx: RequestContext) -> bool:
        """Wait for concurrency semaphore without outliving deadline or cancel_event."""
        if ctx.cancel_event.is_set() or ctx.remaining_ms() <= 0:
            return False

        acq_task = asyncio.create_task(self._semaphore.acquire())
        cancel_task = asyncio.create_task(ctx.cancel_event.wait())
        try:
            done, pending = await asyncio.wait(
                {acq_task, cancel_task},
                timeout=ctx.remaining_s(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if acq_task in done and not acq_task.cancelled():
                if ctx.cancel_event.is_set() or ctx.remaining_ms() <= 0:
                    self._semaphore.release()
                    return False
                return True
            if not acq_task.cancelled() and acq_task.done() and not acq_task.exception():
                self._semaphore.release()
            return False
        except BaseException:
            if not acq_task.done():
                acq_task.cancel()
            if not cancel_task.done():
                cancel_task.cancel()
            raise

    def _select_provider(self, ctx: RequestContext) -> tuple[ModelProvider, SourceType]:
        if ctx.source == "stand-in":
            if self.standin_provider is None:
                raise ProviderError("Stand-in provider not configured", status_code=500)
            return self.standin_provider, "stand-in"

        allowed, bstate = self.breaker.before_call()
        if allowed:
            if self.groq_provider is None:
                raise ProviderError("Groq provider not configured", status_code=500)
            return self.groq_provider, "real"

        if ctx.allow_standin_fallback and self.standin_provider is not None:
            self.bus.record_source_override(ctx.request_id, "stand-in", bstate)
            return self.standin_provider, "stand-in"

        raise ProviderError(
            f"Circuit breaker {bstate}: Groq calls temporarily paused",
            status_code=429,
            is_rate_limit=True,
            is_daily_limit=self.breaker.daily_exhausted,
        )

    async def _invoke_with_retry(
        self,
        ctx: RequestContext,
        *,
        step_name: str,
        messages: list[ChatMessage],
        max_tokens: int,
        temperature: float,
    ) -> ProviderResponse:
        attempt = 0
        while True:
            ctx.check_active()
            remaining_ms = ctx.remaining_ms()
            if remaining_ms <= 20.0:
                raise DeadlineExceededError(f"Insufficient deadline ({remaining_ms:.0f}ms) before {step_name}")

            provider, active_source = self._select_provider(ctx)
            call_timeout_s = min(self.cfg.per_call_timeout_ms, remaining_ms) / 1000.0
            model_id = self.cfg.model_name

            t0 = mono_ms()
            try:
                resp = await provider.chat(
                    messages,
                    model=model_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_s=call_timeout_s,
                    cancel_event=ctx.cancel_event,
                )
                self.bus.record_provider_call(
                    ctx.request_id,
                    ProviderCallRecord(
                        step=step_name,
                        provider=provider.name,
                        model=model_id,
                        status_code=resp.status_code,
                        provider_ttft_ms=resp.provider_ttft_ms,
                        call_total_ms=resp.total_ms,
                        tokens_in=resp.tokens_in,
                        tokens_out=resp.tokens_out,
                        rate_limit_headers=resp.rate_limit.raw,
                    ),
                )
                self.quota.observe_headers(resp.rate_limit)
                self.quota.record_usage(resp.tokens_in + resp.tokens_out)
                if active_source == "real":
                    self.breaker.record_success()
                return resp

            except ProviderError as exc:
                self.bus.record_provider_call(
                    ctx.request_id,
                    ProviderCallRecord(
                        step=step_name,
                        provider=provider.name,
                        model=model_id,
                        status_code=exc.status_code,
                        provider_ttft_ms=None,
                        call_total_ms=max(0.0, mono_ms() - t0),
                        tokens_in=0,
                        tokens_out=0,
                        rate_limit_headers=exc.rate_limit.raw,
                        error=exc.message,
                    ),
                )
                if exc.rate_limit.raw:
                    self.quota.observe_headers(exc.rate_limit)
                if exc.is_rate_limit:
                    self.quota.record_rate_limit(exc.is_daily_limit, exc.retry_after_s)
                if active_source == "real":
                    self.breaker.record_failure(
                        is_rate_limit=exc.is_rate_limit,
                        is_daily_limit=exc.is_daily_limit,
                        retry_after_s=exc.retry_after_s,
                    )

                retryable = (exc.is_rate_limit and not exc.is_daily_limit) or exc.is_timeout or exc.status_code >= 500
                if not retryable or attempt >= self.cfg.max_retries_per_call:
                    raise

                # Compute backoff without resetting or exceeding end-to-end deadline
                # Base exponential backoff capped at max_backoff_ms
                calc_backoff = min(self.cfg.base_backoff_ms * (2**attempt), self.cfg.max_backoff_ms)
                if self.cfg.jitter:
                    calc_backoff *= self._rng.uniform(0.75, 1.25)

                # Provider Retry-After takes precedence and is strictly honored without truncation
                if self.cfg.honor_retry_after and exc.retry_after_s is not None:
                    backoff_ms = max(calc_backoff, exc.retry_after_s * 1000.0)
                else:
                    backoff_ms = calc_backoff

                if backoff_ms + 150.0 >= ctx.remaining_ms():
                    raise DeadlineExceededError(
                        f"Retry backoff ({backoff_ms:.0f}ms) exceeds remaining deadline ({ctx.remaining_ms():.0f}ms)"
                    ) from exc

                attempt += 1
                r_start = mono_ms()
                await self._cancellable_sleep(ctx, backoff_ms / 1000.0)
                r_end = mono_ms()
                self.bus.record_retry(
                    ctx.request_id,
                    attempt=attempt,
                    step=step_name,
                    reason=f"http_{exc.status_code}",
                    start_mono_ms=r_start,
                    end_mono_ms=r_end,
                )

    async def _cancellable_sleep(self, ctx: RequestContext, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(ctx.cancel_event.wait(), timeout=seconds)
            raise RequestCancelledError(ctx.cancel_reason or "cancelled_during_backoff")
        except asyncio.TimeoutError:
            ctx.check_active()
