"""Configurable FAISS + BM25 hybrid retrieval and multi-signal re-ranking tool."""

from __future__ import annotations

import json
import pickle
import re
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from relay.config import INDEXES_DIR, RETRIEVAL_PRESETS, RetrievalPreset
from relay.ingest.chunker import Chunk, chunk_all_papers

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.-]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass
class RetrievedChunk:
    """Chunk returned by retrieval along with score breakdown."""

    chunk: Chunk
    score: float
    dense_score: float = 0.0
    bm25_score: float = 0.0
    rerank_score: float | None = None


class LocalDenseModel:
    """LSA dense semantic embedder with FAISS L2 normalization.

    Uses sublinear TF-IDF + TruncatedSVD for deterministic, lightweight dense embeddings
    that require zero external model downloads or native C++ runtime dependencies.
    """

    def __init__(self, n_components: int = 64) -> None:
        self.n_components = n_components
        self.vectorizer = TfidfVectorizer(
            max_features=4000,
            ngram_range=(1, 2),
            sublinear_tf=True,
            stop_words="english",
        )
        self.svd = TruncatedSVD(n_components=n_components, random_state=42)
        self._fitted = False

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        tfidf = self.vectorizer.fit_transform(texts)
        comp = min(self.n_components, max(8, tfidf.shape[0] - 1))
        if comp != self.n_components:
            self.svd = TruncatedSVD(n_components=comp, random_state=42)
        dense = self.svd.fit_transform(tfidf).astype("float32")
        faiss.normalize_L2(dense)
        self._fitted = True
        return dense

    def transform(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model must be fit before transform")
        tfidf = self.vectorizer.transform(texts)
        dense = self.svd.transform(tfidf).astype("float32")
        faiss.normalize_L2(dense)
        return dense


def _compute_rerank_score(query: str, rc: RetrievedChunk) -> float:
    """Multi-signal re-ranking combining dense similarity, BM25, and term density."""
    q_tokens = set(_tokenize(query))
    c_tokens = set(_tokenize(rc.chunk.text))
    overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))

    # Bonus for exact query numbers or paper short name appearing in text/section
    num_bonus = 0.15 if any(char.isdigit() for char in query if char in rc.chunk.text) else 0.0
    paper_bonus = 0.10 if any(word in rc.chunk.short_name.lower() for word in query.lower().split()) else 0.0

    dense_norm = max(0.0, min(1.0, (rc.dense_score + 1.0) / 2.0))
    bm25_norm = max(0.0, min(1.0, rc.bm25_score / 15.0))

    return (dense_norm * 0.40) + (bm25_norm * 0.35) + (overlap * 0.15) + num_bonus + paper_bonus


class PaperIndex:
    """FAISS + BM25 index for a specific chunking configuration."""

    def __init__(
        self,
        chunk_tokens: int,
        chunk_overlap: int,
        chunks: list[Chunk],
        index: faiss.Index,
        dense_model: LocalDenseModel,
    ) -> None:
        self.chunk_tokens = chunk_tokens
        self.chunk_overlap = chunk_overlap
        self.chunks = chunks
        self.index = index
        self.dense_model = dense_model
        tokenized = [_tokenize(f"{c.paper_title} {c.section} {c.text}") for c in chunks]
        self.bm25 = BM25Okapi(tokenized)

    @classmethod
    def cache_dir(cls, chunk_tokens: int, chunk_overlap: int) -> Path:
        return INDEXES_DIR / f"chunks_{chunk_tokens}_{chunk_overlap}"

    @classmethod
    def load_or_build(cls, chunk_tokens: int, chunk_overlap: int, force: bool = False) -> PaperIndex:
        cdir = cls.cache_dir(chunk_tokens, chunk_overlap)
        chunks_file = cdir / "chunks.json"
        faiss_file = cdir / "faiss.index"
        model_file = cdir / "dense_model.pkl"

        if not force and chunks_file.exists() and faiss_file.exists() and model_file.exists():
            raw = json.loads(chunks_file.read_text(encoding="utf-8"))
            chunks = [Chunk.from_dict(item) for item in raw]
            index = faiss.read_index(str(faiss_file))
            with model_file.open("rb") as f:
                model = pickle.load(f)
            return cls(chunk_tokens, chunk_overlap, chunks, index, model)

        cdir.mkdir(parents=True, exist_ok=True)
        chunks = chunk_all_papers(chunk_tokens=chunk_tokens, chunk_overlap=chunk_overlap)
        texts = [f"{c.paper_title} | {c.section} | {c.text}" for c in chunks]

        model = LocalDenseModel(n_components=64)
        vectors = model.fit_transform(texts)

        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)

        chunks_file.write_text(json.dumps([c.to_dict() for c in chunks], indent=2), encoding="utf-8")
        faiss.write_index(index, str(faiss_file))
        with model_file.open("wb") as f:
            pickle.dump(model, f)

        return cls(chunk_tokens, chunk_overlap, chunks, index, model)

    def search(
        self,
        query: str,
        top_k: int,
        rerank: bool = False,
        hybrid_bm25: bool = True,
        paper_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve top_k passages using dense FAISS, optional BM25 RRF, and optional re-ranking."""
        pool_k = min(len(self.chunks), max(top_k * 4, 18) if (rerank or hybrid_bm25 or paper_filter) else top_k)

        q_vec = self.dense_model.transform([query])
        dense_scores_arr, dense_ids_arr = self.index.search(q_vec, pool_k)

        dense_rank: dict[int, int] = {}
        dense_score_map: dict[int, float] = {}
        for rank, (idx, sc) in enumerate(zip(dense_ids_arr[0], dense_scores_arr[0])):
            if idx < 0:
                continue
            dense_rank[int(idx)] = rank + 1
            dense_score_map[int(idx)] = float(sc)

        candidates: dict[int, float] = {}
        bm25_score_map: dict[int, float] = {}

        if hybrid_bm25:
            q_tokens = _tokenize(query)
            bm25_raw = self.bm25.get_scores(q_tokens)
            top_bm25 = np.argsort(bm25_raw)[::-1][:pool_k]
            bm25_rank = {int(idx): r + 1 for r, idx in enumerate(top_bm25) if bm25_raw[idx] > 0}
            for idx in top_bm25:
                bm25_score_map[int(idx)] = float(bm25_raw[idx])

            all_ids = set(dense_rank) | set(bm25_rank)
            for idx in all_ids:
                rrf = 0.0
                if idx in dense_rank:
                    rrf += 1.0 / (60.0 + dense_rank[idx])
                if idx in bm25_rank:
                    rrf += 1.0 / (60.0 + bm25_rank[idx])
                candidates[idx] = rrf
        else:
            for idx, sc in dense_score_map.items():
                candidates[idx] = sc

        ordered_ids = sorted(candidates, key=lambda i: candidates[i], reverse=True)
        if paper_filter:
            pf = paper_filter.lower()
            filtered = [
                i
                for i in ordered_ids
                if pf in self.chunks[i].paper_id.lower()
                or pf in self.chunks[i].short_name.lower()
                or pf in self.chunks[i].paper_title.lower()
            ]
            if filtered:
                ordered_ids = filtered

        initial_results: list[RetrievedChunk] = []
        for idx in ordered_ids[: (max(top_k * 3, 12) if rerank else top_k)]:
            initial_results.append(
                RetrievedChunk(
                    chunk=self.chunks[idx],
                    score=candidates[idx],
                    dense_score=dense_score_map.get(idx, 0.0),
                    bm25_score=bm25_score_map.get(idx, 0.0),
                )
            )

        if not rerank:
            return initial_results[:top_k]

        for item in initial_results:
            item.rerank_score = _compute_rerank_score(query, item)
            item.score = item.rerank_score

        initial_results.sort(key=lambda x: x.score, reverse=True)
        return initial_results[:top_k]


class IndexRegistry:
    """Caches PaperIndex objects keyed by (chunk_tokens, chunk_overlap)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int], PaperIndex] = {}

    def get_for_preset(self, preset_name: str) -> tuple[PaperIndex, RetrievalPreset]:
        if preset_name not in RETRIEVAL_PRESETS:
            raise KeyError(f"Unknown retrieval preset: {preset_name}")
        preset = RETRIEVAL_PRESETS[preset_name]
        key = (preset.chunk_tokens, preset.chunk_overlap)
        if key not in self._cache:
            self._cache[key] = PaperIndex.load_or_build(preset.chunk_tokens, preset.chunk_overlap)
        return self._cache[key], preset
