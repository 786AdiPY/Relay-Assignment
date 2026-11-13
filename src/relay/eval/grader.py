"""Grading harness mapping pipeline replies onto the 4 required answer_grade buckets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from relay.observability.models import AnswerGradeType


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    expected_type: str  # "answerable" or "unanswerable"
    expected_paper_ids: list[str]
    required_any_terms: list[list[str]]  # List of synonym groups; each group must match >=1 term
    category: str = "standard"
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalQuestion:
        return cls(
            id=d["id"],
            question=d["question"],
            expected_type=d["expected_type"],
            expected_paper_ids=d.get("expected_paper_ids", []),
            required_any_terms=d.get("required_any_terms", []),
            category=d.get("category", "standard"),
            notes=d.get("notes", ""),
        )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def grade_answer(
    q: EvalQuestion,
    *,
    status: str,
    answer_text: str,
    citations: list[str],
    is_refusal: bool,
) -> AnswerGradeType:
    """Assign exact answer_grade enum ('correct', 'wrong', 'refused_correctly', 'refused_wrongly', '')."""
    if status != "ok":
        return ""

    low = _norm(answer_text)
    looks_refusal = is_refusal or any(
        phrase in low
        for phrase in (
            "cannot answer this from the three",
            "could not verify a fully grounded",
            "do not contain verified evidence",
            "not mentioned in the",
            "papers do not state",
            "papers do not say",
            "insufficient_evidence",
        )
    )

    if q.expected_type == "unanswerable":
        return "refused_correctly" if looks_refusal else "wrong"

    if looks_refusal:
        return "refused_wrongly"

    # Must cite at least one expected paper id
    if q.expected_paper_ids:
        joined_cits = " ".join(citations) + " " + answer_text
        if not any(pid in joined_cits for pid in q.expected_paper_ids):
            return "wrong"

    # Every required concept group must match at least one expected phrase/number
    for group in q.required_any_terms:
        if not any(term.lower() in low for term in group):
            return "wrong"

    return "correct"
