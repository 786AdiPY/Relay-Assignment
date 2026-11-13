"""Compact, structured prompts for Steps A, C (Decision/Draft), and D (Verification)."""

from __future__ import annotations

from relay.ingest.retriever import RetrievedChunk
from relay.providers.base import ChatMessage

STEP_A_SYSTEM = (
    "[STEP:A] You analyze questions about three AI papers: "
    "1) Attention Is All You Need (1706.03762v7), "
    "2) Moshi (2410.00037v1), "
    "3) Dynamic Chunking / H-Net (2507.07955v2). "
    "Return ONLY JSON: "
    '{"intent":"paper_qa","target_paper":null|"1706.03762v7"|"2410.00037v1"|"2507.07955v2",'
    '"search_queries":["q1","q2"]}.'
)

STEP_C_DECIDE_SYSTEM = (
    "[STEP:C_DECIDE] Judge whether the retrieved passages contain enough concrete evidence "
    "to answer the user's question WITHOUT guessing from memory. "
    "If clearly out of scope of all three papers, choose 'refuse'. "
    "If partially covered or missing specific numbers/sections, choose 'retrieve_more' with a sharper query. "
    'Return ONLY JSON: {"decision":"answer"|"retrieve_more"|"refuse","reason":"...","followup_query":"..."}.'
)

STEP_C_DRAFT_SYSTEM = (
    "[STEP:C_DRAFT] Answer the question using ONLY the retrieved passages below. "
    "Cite every claim inline using the exact bracketed citation label of the passage (e.g. [Moshi, 2410.00037v1, ... , p.5]). "
    "Do NOT extrapolate or fill gaps from memory. "
    "If the passages do not answer the question, output exactly: INSUFFICIENT_EVIDENCE."
)

STEP_D_VERIFY_SYSTEM = (
    "[STEP:D_VERIFY] Strictly verify the draft answer against the retrieved passages. "
    "FAIL if any factual claim, number, or citation is unsupported by the passages or if citations are missing. "
    'Return ONLY JSON: {"verdict":"PASS"|"FAIL","unsupported_claims":["..."],"feedback":"..."}.'
)


def format_passages(chunks: list[RetrievedChunk], max_chars_per_chunk: int = 1350) -> str:
    """Format retrieved chunks with exact citation labels for prompting."""
    if not chunks:
        return "(no passages retrieved)"
    blocks: list[str] = []
    for idx, item in enumerate(chunks, 1):
        c = item.chunk
        snippet = c.text[:max_chars_per_chunk]
        blocks.append(f"Passage {idx} {c.citation_label()}:\n{snippet}")
    return "\n\n".join(blocks)


def build_step_a_messages(question: str) -> list[ChatMessage]:
    return [
        ChatMessage("system", STEP_A_SYSTEM),
        ChatMessage("user", f"Question: {question}"),
    ]


def build_step_c_decide_messages(question: str, chunks: list[RetrievedChunk]) -> list[ChatMessage]:
    passages = format_passages(chunks, max_chars_per_chunk=700)
    return [
        ChatMessage("system", STEP_C_DECIDE_SYSTEM),
        ChatMessage("user", f"Question: {question}\n\nRetrieved passages:\n{passages}"),
    ]


def build_step_c_draft_messages(
    question: str,
    chunks: list[RetrievedChunk],
    correction_feedback: str = "",
) -> list[ChatMessage]:
    passages = format_passages(chunks, max_chars_per_chunk=1400)
    extra = f"\n\nFix these verification issues in your revised draft:\n{correction_feedback}" if correction_feedback else ""
    return [
        ChatMessage("system", STEP_C_DRAFT_SYSTEM),
        ChatMessage("user", f"Question: {question}\n\nRetrieved passages:\n{passages}{extra}"),
    ]


def build_step_d_verify_messages(
    question: str,
    draft: str,
    chunks: list[RetrievedChunk],
) -> list[ChatMessage]:
    passages = format_passages(chunks, max_chars_per_chunk=1100)
    return [
        ChatMessage("system", STEP_D_VERIFY_SYSTEM),
        ChatMessage("user", f"Question: {question}\n\nDraft answer:\n{draft}\n\nPassages:\n{passages}"),
    ]
