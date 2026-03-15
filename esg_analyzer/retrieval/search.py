"""
search.py
---------
Retrieves the most relevant chunks for a given ESRS disclosure using a
three-tier retrieval strategy:

  Tier 1 : Dense embeddings (sentence-transformers, optional)
    Uses all-MiniLM-L6-v2 (~22 MB, downloads once on first run).
    Handles semantic paraphrasing: "carbon footprint" matches "GHG emissions",
    "workforce diversity" matches "gender pay gap", etc.
    Install: pip install sentence-transformers

  Tier 2 : TF-IDF cosine similarity (scikit-learn, always available)
    Fast, no download, handles bigrams ("scope 3", "net zero", "board diversity").
    Misses synonyms but catches most explicit terminology.

  Tier 3 : Keyword frequency scoring (pure Python, no dependencies)
    Final fallback if sklearn is also unavailable.

Why not a vector database?
  The document is transient (processed once, not stored). We're running ~80
  fixed queries against one document : this does not warrant a vector DB.
  All embeddings are computed in-memory with NumPy cosine similarity.

Why all-MiniLM-L6-v2 specifically?
  - 22 MB model (vs 130 MB for BAAI/bge-small-en)
  - No GPU required, runs on CPU in <2s for a 150-page PDF
  - Strong performance on English technical/business text
  - Ships with sentence-transformers, no extra installs

Tier selection at startup
  We probe for sentence-transformers once at module import time and set
  _RETRIEVAL_TIER accordingly. This avoids per-call import overhead.

Score post-processing
  After any retrieval tier, two adjustments are applied:

  1. Numeric density boost (+0–40%): chunks containing actual numbers with
     ESG units (tCO2e, MWh, %, m³, Mt) are boosted. This addresses the
     "index page problem" : GRI/ESRS appendix pages score highly on keyword
     similarity because they list every metric name, but contain no values.
     Data pages with real numbers should rank above index pages.

  2. Index page penalty (−60%): chunks that look like content indexes
     (many short lines, GRI reference codes, page number patterns) are
     down-weighted so they don't displace actual data.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Dict, List, Optional

from esg_analyzer.parsers.document_parser import TextChunk

logger = logging.getLogger(__name__)

# ── Score post-processing constants ───────────────────────────────────────────

# Regex for numeric values with ESG-relevant units
_NUMERIC_ESG = re.compile(
    r"\b\d[\d,. ]*"
    r"(tco2e|mtco2|mt co2|kt co2|ghg|mwh|gwh|twh|gj|tj|m3|m³|"
    r"litre|liter|gallon|hectare|ha\b|tonne|metric ton|"
    r"fatali|ltifr|trir|%|percent|eur|usd|employees|workers)",
    re.IGNORECASE,
)

# GRI index page signals: "GRI 305-1", "ESRS E1-6", page ref patterns
_INDEX_SIGNALS = re.compile(
    r"(GRI\s+\d{3}-\d|ESRS\s+[EGS]\d|see page \d|p\.\s*\d{1,3}\b"
    r"|disclosure\s+requirement|esrs index|gri content index"
    r"|cross-reference|location in report)",
    re.IGNORECASE,
)


def _adjust_score(text: str, base_score: float) -> float:
    """
    Apply numeric density boost and index-page penalty to a raw similarity score.

    Numeric boost: reward chunks that contain actual ESG numbers.
    Index penalty: penalise chunks that look like GRI/ESRS content indexes.
    These two adjustments together fix the common failure mode where an
    appendix index page ranks above the actual data page.
    """
    lines = text.split("\n")
    n_lines = max(len(lines), 1)

    # ── Index page detection ───────────────────────────────────────────────────
    index_signal_count = len(_INDEX_SIGNALS.findall(text))
    # A typical GRI index page has many short lines and many GRI references
    short_line_ratio = sum(1 for l in lines if len(l.strip()) < 60) / n_lines
    is_index_page = index_signal_count >= 3 or (index_signal_count >= 2 and short_line_ratio > 0.6)

    if is_index_page:
        return base_score * 0.4   # strong penalty : push index pages down

    # ── Numeric density boost ─────────────────────────────────────────────────
    numeric_hits = len(_NUMERIC_ESG.findall(text))
    # Scale: 0 hits → no boost, 3+ hits → +40% max
    boost = min(numeric_hits / 3.0, 1.0) * 0.4
    return base_score * (1.0 + boost)


# ── Tier detection at import time ──────────────────────────────────────────────

_RETRIEVAL_TIER: str
_embedding_model = None   # lazy-loaded on first use

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    _RETRIEVAL_TIER = "embeddings"
    logger.debug("Retrieval: sentence-transformers available → dense embeddings enabled")
except ImportError:
    _RETRIEVAL_TIER = "tfidf"
    logger.debug(
        "Retrieval: sentence-transformers not installed → using TF-IDF. "
        "Install sentence-transformers for improved recall on paraphrased text."
    )


def _get_embedding_model():
    """Lazy-load the embedding model on first retrieval call."""
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading embedding model (all-MiniLM-L6-v2) : first run only…")
        # Suppress noisy but harmless warnings from sentence-transformers and huggingface_hub
        import warnings, os
        _hf_verbosity = os.environ.get("HF_HUB_VERBOSITY")
        os.environ["HF_HUB_VERBOSITY"] = "error"
        logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        # Restore
        if _hf_verbosity is not None:
            os.environ["HF_HUB_VERBOSITY"] = _hf_verbosity
        else:
            os.environ.pop("HF_HUB_VERBOSITY", None)
        logger.info("Embedding model loaded.")
    return _embedding_model


# ── Public API ─────────────────────────────────────────────────────────────────

def retrieve_chunks(
    chunks: List[TextChunk],
    keywords: List[str],
    top_n: int = 5,
    min_score: float = 0.01,
) -> List[Dict]:
    """
    Return the top-N most relevant chunks for a keyword/query set.

    Uses the highest available retrieval tier automatically.

    Returns
    -------
    List of {chunk_id, page, text, score}, sorted by score descending.
    """
    if not chunks or not keywords:
        return []

    if _RETRIEVAL_TIER == "embeddings":
        try:
            return _embedding_retrieve(chunks, keywords, top_n, min_score)
        except Exception as e:
            logger.warning("Embedding retrieval failed (%s) : falling back to TF-IDF", e)

    try:
        return _tfidf_retrieve(chunks, keywords, top_n, min_score)
    except ImportError:
        logger.debug("sklearn not available : using keyword fallback")
    except Exception as e:
        logger.warning("TF-IDF retrieval failed (%s) : falling back to keyword scoring", e)

    return _keyword_retrieve(chunks, keywords, top_n, min_score)


def build_context_window(candidates: List[Dict], max_chars: int = 6000) -> str:
    """Combine top candidate chunks into a page-tagged context string for the LLM."""
    if not candidates:
        return ""
    parts = []
    total = 0
    for c in candidates:
        passage = f"[Page {c['page']}]\n{c['text']}"
        if total + len(passage) > max_chars:
            break
        parts.append(passage)
        total += len(passage)
    return "\n\n---\n\n".join(parts)


# ── Tier 1: Dense embeddings ───────────────────────────────────────────────────

def _embedding_retrieve(
    chunks: List[TextChunk],
    keywords: List[str],
    top_n: int,
    min_score: float,
) -> List[Dict]:
    """
    Encode all chunks and the query with all-MiniLM-L6-v2, then score with
    cosine similarity in NumPy. No vector DB required.

    The query is the keywords joined as a natural phrase, which works well
    for short ESRS keyword lists like ["scope 1", "direct emissions", "tco2e"].
    """
    import numpy as np  # type: ignore  # NumPy ships with scikit-learn anyway

    model = _get_embedding_model()
    query = " ".join(keywords)

    corpus = [c.text for c in chunks]

    # Encode in one batch : significantly faster than encoding one by one
    all_texts = corpus + [query]
    embeddings = model.encode(all_texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)

    chunk_embeddings = embeddings[:-1]   # shape: (n_chunks, dim)
    query_embedding  = embeddings[-1]    # shape: (dim,)

    # Cosine similarity : embeddings are L2-normalised so dot product = cosine sim
    scores = chunk_embeddings @ query_embedding   # shape: (n_chunks,)

    results = [
        {
            "chunk_id": chunks[i].chunk_id,
            "page":     chunks[i].page,
            "text":     chunks[i].text,
            "score":    round(_adjust_score(chunks[i].text, float(scores[i])), 4),
        }
        for i in range(len(chunks))
        if scores[i] >= min_score
    ]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ── Tier 2: TF-IDF ────────────────────────────────────────────────────────────

def _tfidf_retrieve(
    chunks: List[TextChunk],
    keywords: List[str],
    top_n: int,
    min_score: float,
) -> List[Dict]:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.metrics.pairwise import cosine_similarity        # type: ignore

    corpus = [c.text for c in chunks]
    query = " ".join(keywords)

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),   # bigrams catch "scope 3", "net zero", "board diversity"
        min_df=1,
        stop_words="english",
        sublinear_tf=True,    # log dampening for long chunks
    )

    # Fit on corpus + query so query terms get IDF weighting from the document
    tfidf_matrix = vectorizer.fit_transform(corpus + [query])
    query_vec   = tfidf_matrix[-1]
    chunk_vecs  = tfidf_matrix[:-1]

    scores = cosine_similarity(query_vec, chunk_vecs).flatten()

    results = [
        {
            "chunk_id": chunks[i].chunk_id,
            "page":     chunks[i].page,
            "text":     chunks[i].text,
            "score":    round(_adjust_score(chunks[i].text, float(scores[i])), 4),
        }
        for i, s in enumerate(scores)
        if s >= min_score
    ]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ── Tier 3: Keyword frequency (pure Python, no dependencies) ──────────────────

def _keyword_retrieve(
    chunks: List[TextChunk],
    keywords: List[str],
    top_n: int,
    min_score: float,
) -> List[Dict]:
    results = [
        {
            "chunk_id": c.chunk_id,
            "page":     c.page,
            "text":     c.text,
            "score":    round(_adjust_score(c.text, _keyword_score(c.text, keywords)), 4),
        }
        for c in chunks
    ]
    results = [r for r in results if r["score"] >= min_score]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def _keyword_score(text: str, keywords: List[str]) -> float:
    if not keywords:
        return 0.0
    text_lower = text.lower()
    hits = sum(
        1 for kw in keywords
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text_lower)
    )
    base = hits / len(keywords)
    length_boost = math.log1p(len(text.split())) / math.log1p(500)
    return base * (0.7 + 0.3 * length_boost)
