"""
document_parser.py
------------------
Parses ESG/sustainability reports into overlapping text chunks with page numbers.

Supported formats:
  .pdf  : three-tier extraction chain:
            1. pymupdf4llm  → Markdown (best: preserves tables as | col | col |)
            2. PyMuPDF fitz → plain text (good: fast, decent layout)
            3. pdfplumber   → plain text (fallback)
  .html / .htm : via BeautifulSoup

Each chunk carries its source page number, enabling the report to cite
exact locations ("found on page 34").

Key design decisions
--------------------
- pymupdf4llm is preferred because LLMs understand Markdown tables natively.
  Plain-text extraction from a table like:
      Scope 1  |  14,200 tCO2e  |  2023
  becomes "Scope 1 14,200 tCO2e 2023" : unreadable soup to the LLM.
  Markdown preserves the structure, sharply reducing false MISSING detections
  on quantitative disclosures.

- Chunking is done on the FULL document text, NOT page by page.
  The original per-page approach meant evidence spanning page N→N+1 (e.g. a
  Scope 3 table starting on page 42 and continuing on page 43) was split into
  two isolated chunks that never overlapped. Full-document chunking with
  overlap guarantees cross-page context is always preserved.

- Chunk boundaries are nudged to the nearest sentence ending so the LLM
  never receives a chunk that starts or ends mid-sentence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class TextChunk:
    chunk_id: int
    page: int          # page where this chunk starts
    text: str
    word_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.word_count = len(self.text.split())

    def preview(self, n: int = 120) -> str:
        return self.text[:n] + ("…" if len(self.text) > n else "")


@dataclass
class ParsedDocument:
    path: str
    fmt: str           # "pdf" or "html"
    total_pages: int
    total_chunks: int
    chunks: List[TextChunk]
    extraction_method: str = "unknown"   # "markdown", "plain_text", "pdfplumber", "html"

    def __repr__(self) -> str:
        return (
            f"ParsedDocument(fmt={self.fmt!r}, pages={self.total_pages}, "
            f"chunks={self.total_chunks}, method={self.extraction_method!r}, "
            f"path={Path(self.path).name!r})"
        )


# ── Custom exceptions ──────────────────────────────────────────────────────────

class ParseError(Exception):
    """Raised when a document cannot be parsed."""


class UnsupportedFormatError(ParseError):
    """Raised for file types we don't support."""


# ── Public entry point ─────────────────────────────────────────────────────────

def parse_document(
    path: str | Path,
    chunk_words: int = 300,
    overlap_words: int = 75,
    min_chunk_words: int = 20,
) -> ParsedDocument:
    """
    Parse any supported document into overlapping text chunks.

    Parameters
    ----------
    path           : Path to the document (.pdf, .html, .htm)
    chunk_words    : Target chunk size in words
    overlap_words  : Words repeated between adjacent chunks (cross-page context).
                     Increased from original 50 to 75 to better handle evidence
                     that spans page boundaries.
    min_chunk_words: Discard chunks shorter than this (stray headers, page nums)

    Raises
    ------
    FileNotFoundError     : File does not exist
    UnsupportedFormatError: File extension not supported
    ParseError            : All parsing strategies failed
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    if path.stat().st_size == 0:
        raise ParseError(f"File is empty: {path.name}")

    suffix = path.suffix.lower()
    kwargs = dict(chunk_words=chunk_words, overlap_words=overlap_words, min_chunk_words=min_chunk_words)

    if suffix == ".pdf":
        return _parse_pdf(path, **kwargs)
    elif suffix in (".html", ".htm"):
        return _parse_html(path, **kwargs)
    else:
        raise UnsupportedFormatError(
            f"Unsupported format: '{suffix}'. Supported: .pdf, .html, .htm\n"
            "If your report is a .docx, save it as PDF first."
        )


# ── PDF parser : three-tier extraction chain ───────────────────────────────────

def _parse_pdf(path: Path, **kwargs) -> ParsedDocument:
    """
    Try three extraction strategies in descending order of quality.
    Each returns (page_texts, total_pages) where page_texts is
    [(page_number, text), ...].
    """
    strategies = [
        ("markdown",   _extract_markdown),
        ("plain_text", _extract_fitz_plain),
        ("pdfplumber", _extract_pdfplumber),
    ]

    last_error: Optional[Exception] = None

    for method_name, extractor in strategies:
        try:
            page_texts, total_pages = extractor(path)

            if not page_texts:
                logger.warning(
                    "[%s] Extraction returned no text : trying next strategy", method_name
                )
                continue

            chunks = _build_chunks_from_pages(page_texts, **kwargs)

            if not chunks:
                logger.warning(
                    "[%s] Chunking produced no chunks : trying next strategy", method_name
                )
                continue

            if method_name == "markdown":
                logger.info("PDF extracted via Markdown (pymupdf4llm) : tables preserved.")
            else:
                logger.warning(
                    "PDF extracted via '%s' (pymupdf4llm not available or failed). "
                    "Table-heavy reports may have reduced accuracy. "
                    "Install pymupdf4llm for best results: pip install pymupdf4llm",
                    method_name,
                )

            return ParsedDocument(
                path=str(path),
                fmt="pdf",
                total_pages=total_pages,
                total_chunks=len(chunks),
                chunks=chunks,
                extraction_method=method_name,
            )

        except ImportError as e:
            logger.debug("Strategy '%s' not installed: %s", method_name, e)
            last_error = e
        except ParseError:
            raise   # surface explicit parse errors immediately
        except Exception as e:
            logger.warning("Strategy '%s' failed with unexpected error: %s", method_name, e)
            last_error = e

    raise ParseError(
        f"All PDF extraction strategies failed for '{path.name}'. "
        f"Last error: {last_error}\n"
        "Install at least one parser:\n"
        "  pip install pymupdf4llm   ← recommended (Markdown + tables)\n"
        "  pip install pymupdf       ← plain-text fallback\n"
        "  pip install pdfplumber    ← last resort"
    )


# ── Extraction strategies ──────────────────────────────────────────────────────

def _extract_markdown(path: Path) -> Tuple[List[Tuple[int, str]], int]:
    """
    pymupdf4llm: converts each page to Markdown, preserving table structure.

    page_chunks=True returns a list of per-page dicts:
      {"metadata": {"page": 0, ...}, "text": "..."}
    Page numbers are 0-indexed in pymupdf4llm, so we add 1.
    """
    import io
    import sys
    import pymupdf4llm  # type: ignore

    # pymupdf4llm prints "Consider using the pymupdf_layout package…" to stdout.
    # Suppress it : it's a suggestion for a paid add-on, not actionable.
    _stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        page_data = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    finally:
        sys.stdout = _stdout

    if not isinstance(page_data, list):
        raise ParseError("pymupdf4llm returned unexpected type (not a list)")
    if not page_data:
        return [], 0

    page_texts: List[Tuple[int, str]] = []
    for item in page_data:
        page_num = item.get("metadata", {}).get("page", 0) + 1  # 0-indexed → 1-indexed
        text = item.get("text", "").strip()
        if text:
            page_texts.append((page_num, text))

    total_pages = max((p for p, _ in page_texts), default=0)
    return page_texts, total_pages


def _extract_fitz_plain(path: Path) -> Tuple[List[Tuple[int, str]], int]:
    """PyMuPDF plain text : fast, good reading order, no table structure."""
    import fitz  # type: ignore  # PyMuPDF

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ParseError(f"PyMuPDF could not open '{path.name}': {e}") from e

    page_texts: List[Tuple[int, str]] = []
    total_pages = doc.page_count

    try:
        for page_num in range(total_pages):
            page = doc[page_num]
            raw = page.get_text("text")
            cleaned = _clean_text(raw)
            if cleaned:
                page_texts.append((page_num + 1, cleaned))
    finally:
        doc.close()

    return page_texts, total_pages


def _extract_pdfplumber(path: Path) -> Tuple[List[Tuple[int, str]], int]:
    """pdfplumber : last resort, slower but sometimes best on complex layouts."""
    import pdfplumber  # type: ignore

    page_texts: List[Tuple[int, str]] = []
    total_pages = 0

    try:
        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, start=1):
                raw = page.extract_text()
                if not raw:
                    continue
                cleaned = _clean_text(raw)
                if cleaned:
                    page_texts.append((page_num, cleaned))
    except Exception as e:
        raise ParseError(f"pdfplumber could not parse '{path.name}': {e}") from e

    return page_texts, total_pages


# ── HTML parser ────────────────────────────────────────────────────────────────

def _parse_html(path: Path, **kwargs) -> ParsedDocument:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        raise ParseError(
            "BeautifulSoup not installed. Install it: pip install beautifulsoup4 lxml"
        )

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        raise ParseError(f"Could not parse HTML '{path.name}': {e}") from e

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = _clean_text(text)

    if not text:
        raise ParseError(f"HTML file '{path.name}' yielded no text after parsing.")

    chunks = _build_chunks_from_pages([(1, text)], **kwargs)

    return ParsedDocument(
        path=str(path),
        fmt="html",
        total_pages=1,
        total_chunks=len(chunks),
        chunks=chunks,
        extraction_method="html",
    )


# ── Core chunker ───────────────────────────────────────────────────────────────

def _build_chunks_from_pages(
    page_texts: List[Tuple[int, str]],
    chunk_words: int,
    overlap_words: int,
    min_chunk_words: int,
) -> List[TextChunk]:
    """
    Build overlapping chunks from the FULL document text (not page by page).

    We stitch all pages into one word stream while maintaining a word→page
    index so each chunk can report the page where it starts. This fixes the
    cross-page evidence split bug present in the original per-page approach.

    Chunk boundaries are nudged toward sentence endings to avoid mid-sentence
    cuts, using a lightweight regex (no NLTK dependency required).
    """
    if not page_texts:
        return []

    # Stitch all pages into one word list with a parallel page-index array
    all_words: List[str] = []
    word_page: List[int] = []

    for page_num, text in page_texts:
        words = text.split()
        all_words.extend(words)
        word_page.extend([page_num] * len(words))

    if not all_words:
        return []

    step = max(1, chunk_words - overlap_words)
    chunks: List[TextChunk] = []
    chunk_id = 0

    for start in range(0, len(all_words), step):
        raw_end = start + chunk_words
        window_words = all_words[start:raw_end]

        if len(window_words) < min_chunk_words:
            # Tiny trailing fragment : merge into the previous chunk
            if chunks and window_words:
                prev = chunks[-1]
                merged_text = prev.text + " " + " ".join(window_words)
                chunks[-1] = TextChunk(
                    chunk_id=prev.chunk_id,
                    page=prev.page,
                    text=merged_text,
                )
            continue

        # Nudge the end boundary to a sentence ending (±15 words)
        adjusted_end = _find_sentence_boundary(all_words, raw_end, window=15)
        window_words = all_words[start:adjusted_end]

        chunks.append(TextChunk(
            chunk_id=chunk_id,
            page=word_page[start],
            text=" ".join(window_words),
        ))
        chunk_id += 1

    return chunks


# ── Sentence boundary detection ────────────────────────────────────────────────

_SENTENCE_END = re.compile(r'[.!?]["\')\]]?$')
# Avoid treating decimal numbers ("14,200.5") and all-caps abbreviations
# ("GHG." "tCO2e." "Inc.") as sentence endings
_NOT_SENTENCE_END = re.compile(r'\d[.,]\d|^[A-Z]{1,5}\.$|^\d+\.$')


def _find_sentence_boundary(words: List[str], pos: int, window: int) -> int:
    """
    Search for a sentence-ending word near pos.
    Searches forward first (prefers longer chunks), then backward.
    Returns adjusted position within [pos-window, pos+window], or pos if none found.
    """
    n = len(words)
    # Clamp pos to valid range : can be == n when pointing past the last word
    pos = min(pos, n)
    lo = max(0, pos - window)
    hi = min(n, pos + window)

    for i in range(pos, hi):
        if _is_sentence_end(words[i]):
            return i + 1

    for i in range(pos - 1, lo - 1, -1):
        if _is_sentence_end(words[i]):
            return i + 1

    return pos


def _is_sentence_end(word: str) -> bool:
    if not _SENTENCE_END.search(word):
        return False
    if _NOT_SENTENCE_END.search(word):
        return False
    return True


# ── Text cleaning ──────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Normalise whitespace and remove common PDF extraction artefacts."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)   # collapse all whitespace
    text = text.replace("\x00", "")    # remove null bytes (PyMuPDF artefact)
    return text.strip()
