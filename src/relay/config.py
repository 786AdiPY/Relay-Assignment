"""Central configuration, paper specs, retrieval presets, and scenario definitions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
PAPERS_DIR = PROJECT_ROOT / "papers"
INDEXES_DIR = DATA_DIR / "indexes"
QUESTIONS_PATH = DATA_DIR / "questions.json"
RUNS_DIR = PROJECT_ROOT / "runs"


@dataclass(frozen=True)
class PaperSpec:
    """Exact arXiv paper specification required by the brief."""

    paper_id: str
    arxiv_id: str
    version: int
    title: str
    short_name: str

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.arxiv_id}v{self.version}.pdf"

    @property
    def pdf_path(self) -> Path:
        return PAPERS_DIR / f"{self.arxiv_id}v{self.version}.pdf"


PAPERS: tuple[PaperSpec, ...] = (
    PaperSpec(
        paper_id="1706.03762v7",
        arxiv_id="1706.03762",
        version=7,
        title="Attention Is All You Need",
        short_name="Attention (Vaswani et al.)",
    ),
    PaperSpec(
        paper_id="2410.00037v1",
        arxiv_id="2410.00037",
        version=1,
        title="Moshi: a speech-text foundation model for real-time dialogue",
        short_name="Moshi",
    ),
    PaperSpec(
        paper_id="2507.07955v2",
        arxiv_id="2507.07955",
        version=2,
        title="Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        short_name="Dynamic Chunking (H-Net)",
    ),
)


@dataclass(frozen=True)
class RetrievalPreset:
    """Configurable retrieval setting for Table 3 experiments."""

    name: str
    chunk_tokens: int
    chunk_overlap: int
    top_k: int
    rerank: bool
    hybrid_bm25: bool = True


RETRIEVAL_PRESETS: dict[str, RetrievalPreset] = {
    "small_k3_norank": RetrievalPreset(
        name="small_k3_norank",
        chunk_tokens=180,
        chunk_overlap=30,
        top_k=3,
        rerank=False,
        hybrid_bm25=False,  # Pure naive dense FAISS baseline
    ),
    "med_k5_rerank": RetrievalPreset(
        name="med_k5_rerank",
        chunk_tokens=320,
        chunk_overlap=60,
        top_k=5,
        rerank=True,
        hybrid_bm25=True,
    ),
    "large_k8_norank": RetrievalPreset(
        name="large_k8_norank",
        chunk_tokens=550,
        chunk_overlap=90,
        top_k=8,
        rerank=False,
        hybrid_bm25=False,
    ),
    "med_k3_degraded": RetrievalPreset(
        name="med_k3_degraded",
        chunk_tokens=320,
        chunk_overlap=60,
        top_k=3,
        rerank=False,
        hybrid_bm25=True,
    ),
}


@dataclass(frozen=True)
class GroqModelPricing:
    """Published Groq pricing ($ per 1M tokens) for cost-per-1k-answer calculation."""

    model_id: str
    input_per_1m_usd: float
    output_per_1m_usd: float


DEFAULT_GROQ_MODEL = os.getenv("RELAY_GROQ_MODEL", "qwen/qwen3.8-27b")

GROQ_PRICING: dict[str, GroqModelPricing] = {
    "openai/gpt-oss-20b": GroqModelPricing("openai/gpt-oss-20b", 0.075, 0.10),
    "openai/gpt-oss-120b": GroqModelPricing("openai/gpt-oss-120b", 0.15, 0.60),
    "qwen/qwen3.8-27b": GroqModelPricing("qwen/qwen3.8-27b", 0.10, 0.20),
    "llama-3.1-8b-instant": GroqModelPricing("llama-3.1-8b-instant", 0.05, 0.08),
}


def get_groq_api_key() -> str:
    """Return Groq API key from environment without caching secrets in logs."""
    return os.getenv("GROQ_API_KEY", "").strip()
