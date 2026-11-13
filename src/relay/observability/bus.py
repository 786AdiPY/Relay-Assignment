"""Layer 5 passive out-of-band telemetry event bus and disk recorders."""

from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    RESULTS_CSV_COLUMNS,
    AnswerGradeType,
    ProviderCallRecord,
    RequestTrace,
    RetrySpan,
    SourceType,
    StatusType,
    StepSpan,
)


def utc_now_iso() -> str:
    """Return ISO-8601 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mono_ms() -> float:
    """Monotonic clock in milliseconds for duration and offset measurements."""
    return time.perf_counter() * 1000.0


class TelemetryBus:
    """Out-of-band observer collecting lifecycle events from Layers 1-4.

    Layers 1-4 push lightweight events here without routing requests through
    Layer 5 or letting Layer 5 alter control flow.
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._traces: dict[str, RequestTrace] = {}
        self._ordered_ids: list[str] = []
        self._peak_queue_by_scenario: dict[str, int] = {}
        self.output_dir = output_dir

    def record_arrival(
        self,
        request_id: str,
        scenario: str,
        source: SourceType,
        deadline_ms: float,
        retrieval_setting: str,
        question_id: str = "",
    ) -> RequestTrace:
        """Register request arrival before Layer 2 queue admission."""
        now_m = mono_ms()
        trace = RequestTrace(
            request_id=request_id,
            scenario=scenario,
            source=source,
            question_id=question_id,
            retrieval_setting=retrieval_setting,
            start_time=utc_now_iso(),
            arrival_mono_ms=now_m,
            deadline_ms=deadline_ms,
        )
        with self._lock:
            self._traces[request_id] = trace
            self._ordered_ids.append(request_id)
        return trace

    def _offset(self, trace: RequestTrace) -> float:
        return max(0.0, mono_ms() - trace.arrival_mono_ms)

    def record_queue_enter(self, request_id: str, queue_depth: int) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            trace.queue_enter_offset_ms = self._offset(trace)
            trace.queue_depth_at_enter = queue_depth
            prev = self._peak_queue_by_scenario.get(trace.scenario, 0)
            if queue_depth > prev:
                self._peak_queue_by_scenario[trace.scenario] = queue_depth

    def record_queue_exit(self, request_id: str, in_flight: int) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            exit_off = self._offset(trace)
            trace.queue_exit_offset_ms = exit_off
            if trace.queue_enter_offset_ms is not None:
                trace.queue_wait_ms = max(0.0, exit_off - trace.queue_enter_offset_ms)
            trace.in_flight_at_exit = in_flight

    def record_step(
        self,
        request_id: str,
        step: str,
        start_mono_ms: float,
        end_mono_ms: float,
        loop_idx: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            start_off = max(0.0, start_mono_ms - trace.arrival_mono_ms)
            end_off = max(start_off, end_mono_ms - trace.arrival_mono_ms)
            trace.steps.append(
                StepSpan(
                    step=step,
                    loop_idx=loop_idx,
                    start_offset_ms=start_off,
                    end_offset_ms=end_off,
                    duration_ms=max(0.0, end_mono_ms - start_mono_ms),
                    meta=meta or {},
                )
            )

    def record_retry(
        self,
        request_id: str,
        attempt: int,
        step: str,
        reason: str,
        start_mono_ms: float,
        end_mono_ms: float,
    ) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            start_off = max(0.0, start_mono_ms - trace.arrival_mono_ms)
            end_off = max(start_off, end_mono_ms - trace.arrival_mono_ms)
            trace.retries += 1
            trace.retry_events.append(
                RetrySpan(
                    attempt=attempt,
                    step=step,
                    reason=reason,
                    start_offset_ms=start_off,
                    end_offset_ms=end_off,
                    backoff_ms=max(0.0, end_mono_ms - start_mono_ms),
                )
            )

    def record_provider_call(self, request_id: str, call: ProviderCallRecord) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            trace.provider_calls.append(call)
            trace.tokens_in += call.tokens_in
            trace.tokens_out += call.tokens_out

    def record_source_override(self, request_id: str, source: SourceType, circuit_state: str) -> None:
        """Update effective source (e.g., stand-in fallback when circuit breaker opens)."""
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            trace.source = source
            trace.circuit_state = circuit_state

    def record_degradation(self, request_id: str, mode: str, effective_retrieval: str) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return
            trace.degraded_mode = mode
            trace.retrieval_setting = effective_retrieval

    def record_first_verified_token(self, request_id: str) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace or trace.first_verified_token_offset_ms is not None:
                return
            trace.first_verified_token_offset_ms = self._offset(trace)

    def record_cancellation(self, request_id: str, reason: str) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace or trace.cancelled_offset_ms is not None:
                return
            trace.cancelled_offset_ms = self._offset(trace)
            trace.cancel_reason = reason

    def record_completion(
        self,
        request_id: str,
        status: StatusType,
        answer_text: str = "",
        citations: list[str] | None = None,
        answer_grade: AnswerGradeType = "",
        error_detail: str = "",
        circuit_state: str | None = None,
    ) -> RequestTrace | None:
        with self._lock:
            trace = self._traces.get(request_id)
            if not trace:
                return None
            trace.completion_offset_ms = self._offset(trace)
            trace.status = status
            trace.answer_text = answer_text
            trace.citations = citations or []
            trace.answer_grade = answer_grade
            trace.error_detail = error_detail
            if circuit_state is not None:
                trace.circuit_state = circuit_state
            return trace

    def update_grade(self, request_id: str, grade: AnswerGradeType) -> None:
        with self._lock:
            trace = self._traces.get(request_id)
            if trace:
                trace.answer_grade = grade

    def traces(self) -> list[RequestTrace]:
        with self._lock:
            return [self._traces[rid] for rid in self._ordered_ids]

    def peak_queue_by_scenario(self) -> dict[str, int]:
        with self._lock:
            return dict(self._peak_queue_by_scenario)

    def flush(self, output_dir: Path | None = None, append: bool = False) -> tuple[Path, Path]:
        """Write strict 12-column results.csv and full traces.jsonl cleanly."""
        target = output_dir or self.output_dir
        if target is None:
            raise ValueError("TelemetryBus.flush requires an output_dir")
        target.mkdir(parents=True, exist_ok=True)
        csv_path = target / "results.csv"
        jsonl_path = target / "traces.jsonl"

        traces = self.traces()
        mode = "a" if append and csv_path.exists() else "w"
        write_header = mode == "w" or csv_path.stat().st_size == 0

        with csv_path.open(mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULTS_CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            for tr in traces:
                writer.writerow(tr.to_csv_row())

        jmode = "a" if append and jsonl_path.exists() else "w"
        with jsonl_path.open(jmode, encoding="utf-8") as f:
            for tr in traces:
                f.write(json.dumps(tr.to_json_dict(), ensure_ascii=False) + "\n")

        return csv_path, jsonl_path
