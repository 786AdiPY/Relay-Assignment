"""Paper chunking, indexing, and configurable FAISS/BM25 retrieval."""

from .chunker import Chunk, chunk_all_papers, chunk_paper
from .fetcher import fetch_all_papers, fetch_paper
from .retriever import IndexRegistry, LocalDenseModel, PaperIndex, RetrievedChunk

LocalEmbedder = LocalDenseModel

__all__ = [
    "Chunk",
    "IndexRegistry",
    "LocalDenseModel",
    "LocalEmbedder",
    "PaperIndex",
    "RetrievedChunk",
    "chunk_all_papers",
    "chunk_paper",
    "fetch_all_papers",
    "fetch_paper",
]
