"""Layer 5 data models matching the required 12-column results.csv and trace spans."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceType = Literal["real", "stand-in"]
StatusType = Literal["ok", "rate_limited", "timeout", "error"]
AnswerGradeType = Literal["correct", "wrong", "refused_correctly", "refused_wrongly", ""]

RESULTS_CSV_COLUMNS: tuple[str, ...] = (
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
)


@dataclass
class StepSpan:
    """Single step execution window relative to request arrival."""

    step: str
    loop_idx: int
    start_offset_ms: float
    end_offset_ms: float
    duration_ms: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrySpan:
    """Single retry attempt window relative to request arrival."""

    attempt: int
    step: str
    reason: str
    start_offset_ms: float
    end_offset_ms: float
    backoff_ms: float


@dataclass
class ProviderCallRecord:
    """Per-model-call telemetry emitted by Layer 4."""

    step: str
    provider: str
    model: str
    status_code: int
    provider_ttft_ms: float | None
    call_total_ms: float
    tokens_in: int
    tokens_out: int
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RequestTrace:
    """Full lifecycle trace recorded out-of-band by Layer 5 for one request."""

    request_id: str
    scenario: str
    source: SourceType
    question_id: str = ""
    retrieval_setting: str = ""
    start_time: str = ""
    arrival_mono_ms: float = 0.0
    deadline_ms: float = 0.0
    queue_enter_offset_ms: float | None = None
    queue_exit_offset_ms: float | None = None
    queue_wait_ms: float = 0.0
    queue_depth_at_enter: int = 0
    in_flight_at_exit: int = 0
    first_verified_token_offset_ms: float | None = None
    completion_offset_ms: float | None = None
    cancelled_offset_ms: float | None = None
    cancel_reason: str | None = None
    status: StatusType = "error"
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    degraded_mode: str = "none"
    circuit_state: str = "CLOSED"
    answer_text: str = ""
    citations: list[str] = field(default_factory=list)
    answer_grade: AnswerGradeType = ""
    error_detail: str = ""
    steps: list[StepSpan] = field(default_factory=list)
    retry_events: list[RetrySpan] = field(default_factory=list)
    provider_calls: list[ProviderCallRecord] = field(default_factory=list)

    def to_csv_row(self) -> dict[str, Any]:
        """Project trace into the strict 12-column results.csv row."""
        ft = (
            round(self.first_verified_token_offset_ms, 2)
            if self.first_verified_token_offset_ms is not None
            else ""
        )
        tot = round(self.completion_offset_ms, 2) if self.completion_offset_ms is not None else 0.0
        return {
            "request_id": self.request_id,
            "scenario": self.scenario,
            "source": self.source,
            "start_time": self.start_time,
            "first_token_ms": ft,
            "total_ms": tot,
            "status": self.status,
            "retries": self.retries,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "retrieval_setting": self.retrieval_setting,
            "answer_grade": self.answer_grade,
        }

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize complete trace including nested step and retry spans."""
        return asdict(self)
