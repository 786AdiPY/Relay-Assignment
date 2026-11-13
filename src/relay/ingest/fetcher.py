"""Download and verify exact arXiv PDF versions required by The Relay."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from pypdf import PdfReader

from relay.config import PAPERS, PAPERS_DIR, PaperSpec

logger = logging.getLogger(__name__)


async def fetch_paper(spec: PaperSpec, client: httpx.AsyncClient, force: bool = False) -> Path:
    """Download a single arXiv PDF at its exact version and verify readability."""
    spec.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.pdf_path.exists() and not force:
        reader = PdfReader(str(spec.pdf_path))
        if len(reader.pages) > 0:
            return spec.pdf_path

    headers = {"User-Agent": "TheRelayAssignment/0.1 (research paper retrieval pipeline)"}
    resp = await client.get(spec.pdf_url, headers=headers, follow_redirects=True, timeout=60.0)
    resp.raise_for_status()
    content = resp.content
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"Downloaded content for {spec.paper_id} is not a valid PDF")

    spec.pdf_path.write_bytes(content)
    reader = PdfReader(str(spec.pdf_path))
    if len(reader.pages) == 0:
        raise ValueError(f"Downloaded PDF for {spec.paper_id} has 0 pages")
    logger.info("Fetched %s (%d pages)", spec.paper_id, len(reader.pages))
    return spec.pdf_path


async def fetch_all_papers(force: bool = False) -> dict[str, Path]:
    """Verify or download all three required arXiv papers at exact versions."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    async with httpx.AsyncClient() as client:
        for spec in PAPERS:
            results[spec.paper_id] = await fetch_paper(spec, client, force=force)
    return results
