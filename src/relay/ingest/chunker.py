"""Section- and page-aware PDF text extraction and configurable chunking."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pypdf import PdfReader

from relay.config import PAPERS, PaperSpec

_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)+)\s+[A-Z][^\n]{2,70}|Abstract|Introduction|Conclusion|References|Appendix[^\n]*)$"
)


@dataclass
class Chunk:
    """Single indexed passage with full citation metadata."""

    chunk_id: str
    paper_id: str
    paper_title: str
    short_name: str
    version: int
    page_num: int
    section: str
    text: str
    word_count: int

    def citation_label(self) -> str:
        return f"[{self.short_name}, {self.paper_id}, {self.section}, p.{self.page_num}]"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Chunk:
        return cls(**data)


def _clean_page_text(raw: str) -> str:
    """Normalize hyphenated line breaks and excessive whitespace without losing structure."""
    text = re.sub(r"-\n([a-z])", r"\1", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_heading(line: str) -> str | None:
    stripped = line.strip()
    if len(stripped) < 4 or len(stripped) > 85:
        return None
    if _HEADING_RE.match(stripped):
        return stripped
    return None


def extract_paper_pages(spec: PaperSpec) -> list[tuple[int, str, str]]:
    """Extract (page_num, active_section, text) tuples from a PDF."""
    if not spec.pdf_path.exists():
        raise FileNotFoundError(f"Missing paper PDF: {spec.pdf_path}")

    reader = PdfReader(str(spec.pdf_path))
    pages: list[tuple[int, str, str]] = []
    current_section = "Title / Abstract"
    for idx, page in enumerate(reader.pages):
        page_num = idx + 1
        raw_text = page.extract_text() or ""
        cleaned = _clean_page_text(raw_text)
        if not cleaned:
            continue
        for line in cleaned.splitlines():
            heading = _detect_heading(line)
            if heading:
                current_section = heading
                break
        pages.append((page_num, current_section, cleaned))
    return pages


def chunk_paper(spec: PaperSpec, chunk_tokens: int, chunk_overlap: int) -> list[Chunk]:
    """Split a paper into overlapping chunks preserving section and page boundaries.

    Uses ~0.75 words/token heuristic so word limits map reliably to token budgets.
    """
    max_words = max(40, int(chunk_tokens * 0.75))
    overlap_words = max(0, min(max_words // 2, int(chunk_overlap * 0.75)))
    step = max(20, max_words - overlap_words)

    pages = extract_paper_pages(spec)
    chunks: list[Chunk] = []
    current_section = "Title / Abstract"

    for page_num, page_section, text in pages:
        current_section = page_section
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        buffer_words: list[str] = []
        chunk_idx = 0

        for para in paragraphs:
            first_line = para.splitlines()[0]
            heading = _detect_heading(first_line)
            if heading:
                current_section = heading

            words = para.split()
            buffer_words.extend(words)

            while len(buffer_words) >= max_words:
                window = buffer_words[:max_words]
                chunk_idx += 1
                chunks.append(
                    Chunk(
                        chunk_id=f"{spec.paper_id}:p{page_num}:c{chunk_idx}",
                        paper_id=spec.paper_id,
                        paper_title=spec.title,
                        short_name=spec.short_name,
                        version=spec.version,
                        page_num=page_num,
                        section=current_section,
                        text=" ".join(window),
                        word_count=len(window),
                    )
                )
                buffer_words = buffer_words[step:]

        if buffer_words:
            chunk_idx += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{spec.paper_id}:p{page_num}:c{chunk_idx}",
                    paper_id=spec.paper_id,
                    paper_title=spec.title,
                    short_name=spec.short_name,
                    version=spec.version,
                    page_num=page_num,
                    section=current_section,
                    text=" ".join(buffer_words),
                    word_count=len(buffer_words),
                )
            )

    return chunks


def chunk_all_papers(chunk_tokens: int, chunk_overlap: int) -> list[Chunk]:
    """Chunk all three library papers for a given chunking configuration."""
    all_chunks: list[Chunk] = []
    for spec in PAPERS:
        all_chunks.extend(chunk_paper(spec, chunk_tokens=chunk_tokens, chunk_overlap=chunk_overlap))
    return all_chunks
