"""
detector.py
-----------
For each ESRS disclosure, retrieves candidate passages and asks the LLM
to determine FOUND / PARTIAL / MISSING with evidence.

Architecture
------------
  DetectionResult   : typed dataclass — no dict sprawl
  _build_prompt()   : pure function, testable in isolation
  _parse_response() : pure function, testable in isolation
  detect_one_async(): single disclosure — async, single responsibility
  detect_all()      : public entry point — runs all disclosures concurrently
                      via asyncio + semaphore (respects API rate limits)

Concurrency model
-----------------
  detect_all() is async internally but exposes a synchronous wrapper so
  callers (main.py) don't need to know about asyncio.

  A semaphore caps concurrent LLM calls at MAX_CONCURRENT (default 6).
  This is deliberately conservative:
    - Anthropic Tier 1 allows ~5 req/s
    - OpenAI Tier 1 allows ~3 req/s  
    - Ollama is local so concurrency is limited by CPU/RAM
  Tune MAX_CONCURRENT via the env var ESG_MAX_CONCURRENT if needed.

  Result: ~80 disclosures at 6 concurrent → ~2-3 min vs ~7 min sequential.

JSON parsing resilience
-----------------------
  _parse_response() uses a four-stage recovery strategy:
    1. Direct json.loads (the happy path)
    2. Strip common fence patterns (```json, ```, etc.)
    3. Extract the first {...} block with regex
    4. Keyword fallback if all JSON parsing fails
  This handles all known LLM JSON formatting quirks without structured outputs,
  keeping Ollama and other providers that don't support response_format compatible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from esg_analyzer.llm_provider import LLMConfig, LLMError, call_llm_async
from esg_analyzer.parsers.document_parser import ParsedDocument
from esg_analyzer.retrieval.search import build_context_window, retrieve_chunks

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"FOUND", "PARTIAL", "MISSING"})

# Cap concurrent LLM calls. Override via ESG_MAX_CONCURRENT env var.
_DEFAULT_MAX_CONCURRENT = 6


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    key: str
    section: str
    name: str
    category: str
    pillar: str
    is_quantitative: bool
    weight_original: int
    weight_omnibus: int
    omnibus_notes: str
    status: str
    best_quote: Optional[str]
    page: Optional[int]
    reason: str
    quality_flags: List[str]       = field(default_factory=list)
    data_points_found: List[str]   = field(default_factory=list)
    data_points_missing: List[str] = field(default_factory=list)
    top_candidate_pages: List[int] = field(default_factory=list)
    is_mandatory: bool             = False
    mode: str                      = "original"
    used_fallback: bool            = False
    cross_references: Dict         = field(default_factory=dict)
    ig3: Dict                      = field(default_factory=dict)


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert ESG analyst evaluating whether ESRS disclosures are present in a sustainability report.

Your job is DETECTION, not auditing. The distinction matters:
- FOUND means the disclosure EXISTS with reasonable substance — the company has reported on this topic with actual data or concrete description
- PARTIAL means the topic is mentioned but lacks substance — vague statements, targets without numbers, topics referenced but not quantified
- MISSING means the topic is genuinely absent from the report

Respond ONLY with valid JSON — no markdown fences, no preamble, no postamble. Raw JSON only.

Required JSON structure:
{
  "status": "FOUND" | "PARTIAL" | "MISSING",
  "best_quote": "<verbatim excerpt MAX 120 chars proving presence, or null if MISSING>",
  "page": <integer or null>,
  "reason": "<1 sentence explaining your verdict>",
  "quality_flags": ["<compliance gap>", ...],
  "data_points_found": ["<confirmed data point>", ...],
  "data_points_missing": ["<expected but absent>", ...]
}

Status calibration — be precise:
  FOUND   - The disclosure is substantively present. The company has reported actual numbers,
            named methodology, or provided concrete description. Some expected data points may
            be missing (note them in quality_flags) but the core disclosure EXISTS.
            Example: Scope 1 = 14,200 tCO2e reported → FOUND even if methodology isn't named.

  PARTIAL - The topic is touched but lacks substance. Vague language, commitments without
            figures, or a single data point when several are clearly needed.
            Example: "We are committed to reducing emissions" with no figures → PARTIAL.

  MISSING - The topic is genuinely not addressed anywhere in the provided passages.
            Do NOT use MISSING if you found any relevant numbers or concrete statements.

Quality flags (report these regardless of FOUND/PARTIAL/MISSING status):
  - Vague commitment without numbers
  - Net-zero claim without interim milestones
  - Missing baseline year for a target
  - Missing methodology for emissions calculation
  - Scope 3 total disclosed but categories not enumerated
  - Offsets mentioned without type or standard
  - Market-based Scope 2 missing (only location-based provided)

Important: FOUND with quality_flags is the correct output for a disclosure that exists
but has compliance gaps. Do not downgrade to PARTIAL just because flags are present.

Critical — data tables vs index pages:
Many sustainability reports include a GRI/ESRS content index appendix that LISTS metric
names (e.g. "Scope 1 GHG emissions... see page 108") without providing actual values.
If the context contains BOTH an index/reference passage AND a passage with actual numbers
or tables, base your verdict on the passage with actual data, not the index entry.
A page showing "32.4 Mt CO2e | 35.1 Mt CO2e" IS the disclosure. A page saying
"Scope 1 emissions disclosed on page 108" is NOT — ignore it for your verdict.

"""


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(disclosure: Dict[str, Any], context: str, extraction_method: str = "markdown") -> str:
    # Only add the table-parsing hint when pdfplumber was used.
    # pymupdf4llm produces clean markdown tables that don't need it,
    # and adding the hint confuses the LLM on well-formatted input (causes MISSING regressions).
    if extraction_method == "pdfplumber":
        table_hint = (
            "\nNote — PDF table quality: This document was extracted with a basic PDF parser. "
            "Tables may appear as space-separated values without clear column alignment, e.g. "
            "'Scope 1- Direct emissions Mt CO2e 42 38 34 37 32 33' means Scope 1 = 33 Mt CO2e "
            "(most recent year, rightmost value). If a metric name and numbers appear together, "
            "treat that as substantive data even if formatting is imperfect.\n"
        )
    else:
        table_hint = ""

    value_hint = disclosure.get("value_hint", "")
    hint_line = f"  Search hint        : {value_hint}\n" if value_hint else ""

    return (
        f"ESRS Disclosure to evaluate:\n"
        f"  Section     : {disclosure.get('section', '')}\n"
        f"  Name        : {disclosure.get('name', '')}\n"
        f"  Description : {disclosure.get('description', '')}\n"
        f"  Expected data points: {', '.join(disclosure.get('expected_data_points', []))}\n"
        f"  Expected units      : {', '.join(disclosure.get('expected_units', [])) or 'N/A'}\n"
        f"{hint_line}"
        f"{table_hint}"
        f"\nPassages from the ESG report:\n{context}\n\n"
        f"Respond with JSON only."
    )


# ── JSON parser — four-stage recovery ─────────────────────────────────────────

# Matches the first complete JSON object in a string
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
# Common fence variants that LLMs emit
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_response(raw: str, key: str) -> Dict[str, Any]:
    """
    Parse LLM JSON with four-stage recovery. Returns {} only if all stages fail.

    Stage 1: Direct parse (happy path — well-behaved LLM output)
    Stage 2: Strip markdown fences (```json ... ```)
    Stage 3: Extract first {...} block with regex (preamble/postamble present)
    Stage 4: Give up, return {} — caller will use keyword fallback
    """
    if not raw or not raw.strip():
        logger.warning("[%s] LLM returned empty response", key)
        return {}

    attempts = [
        ("direct",       raw.strip()),
        ("fence-strip",  _FENCE_RE.sub("", raw).strip()),
    ]

    # Stage 3: regex extraction of first JSON object
    match = _JSON_OBJECT_RE.search(raw)
    if match:
        attempts.append(("regex-extract", match.group(0)))

    for stage, candidate in attempts:
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                continue
            # Normalise status field
            status = parsed.get("status", "")
            if isinstance(status, str):
                parsed["status"] = status.upper().strip()
            if parsed.get("status") not in VALID_STATUSES:
                logger.warning(
                    "[%s] Unexpected status %r — defaulting to MISSING", key, parsed.get("status")
                )
                parsed["status"] = "MISSING"
            # Clamp best_quote length defensively
            if parsed.get("best_quote") and len(parsed["best_quote"]) > 300:
                parsed["best_quote"] = parsed["best_quote"][:300] + "…"
            return parsed
        except (json.JSONDecodeError, ValueError):
            continue

    logger.warning(
        "[%s] All JSON parse stages failed. Using keyword fallback. raw=%r",
        key, raw[:200],
    )
    return {}


# ── Core async detection ───────────────────────────────────────────────────────

async def _detect_one_async(
    key: str,
    disclosure: Dict[str, Any],
    doc: ParsedDocument,
    llm_config: LLMConfig,
    semaphore: asyncio.Semaphore,
    top_n_chunks: int = 8,
) -> DetectionResult:
    """
    Detect a single ESRS disclosure asynchronously.
    The semaphore ensures we never exceed MAX_CONCURRENT parallel LLM calls.

    Retrieval strategy: multi-query. We run up to 3 retrieval passes —
    one for the primary keywords, one per major expected data point —
    then deduplicate and take the top chunks by score. This ensures that
    a disclosure whose evidence is spread across multiple sections (e.g.
    Scope 1 value on page 12, methodology on page 34) still gets both into
    the context window.
    """
    base = _base_fields(key, disclosure)

    # ── Multi-query retrieval ──────────────────────────────────────────────────
    candidates = _multi_query_retrieve(doc, disclosure, top_n=top_n_chunks)
    base["top_candidate_pages"] = [c["page"] for c in candidates[:3]]

    if not candidates:
        return DetectionResult(
            **base,
            status="MISSING",
            best_quote=None,
            page=None,
            reason="No relevant passages found in the document.",
            data_points_missing=disclosure.get("expected_data_points", []),
        )

    context = build_context_window(candidates, max_chars=6000)
    prompt = _build_prompt(disclosure, context, extraction_method=doc.extraction_method)
    used_fallback = False

    async with semaphore:
        try:
            raw = await call_llm_async(_SYSTEM_PROMPT, prompt, llm_config)
            parsed = _parse_response(raw, key)
            if not parsed:
                used_fallback = True
                parsed = _keyword_fallback(candidates, disclosure)
        except LLMError as e:
            logger.warning("[%s] LLM failed: %s", key, e)
            used_fallback = True
            parsed = _keyword_fallback(candidates, disclosure)
        except Exception as e:
            # Catch-all: never let one disclosure crash the entire run
            logger.error("[%s] Unexpected error during detection: %s", key, e, exc_info=True)
            used_fallback = True
            parsed = _keyword_fallback(candidates, disclosure)

    return DetectionResult(
        **base,
        status=parsed.get("status", "MISSING"),
        best_quote=parsed.get("best_quote"),
        page=parsed.get("page"),
        reason=parsed.get("reason", ""),
        quality_flags=parsed.get("quality_flags", []),
        data_points_found=parsed.get("data_points_found", []),
        data_points_missing=parsed.get("data_points_missing", []),
        used_fallback=used_fallback,
    )


# ── Orchestration ──────────────────────────────────────────────────────────────

async def _detect_all_async(
    schema: Dict[str, Any],
    doc: ParsedDocument,
    llm_config: LLMConfig,
    mode: str,
    max_concurrent: int,
) -> List[DetectionResult]:
    """
    Run all disclosure checks concurrently, bounded by a semaphore.
    Progress is logged as tasks complete (not in schema order).
    """
    disclosures = {k: v for k, v in schema.items() if not k.startswith("_")}
    total = len(disclosures)
    semaphore = asyncio.Semaphore(max_concurrent)
    completed = 0

    logger.info(
        "Starting async detection: %d disclosures, max %d concurrent LLM calls",
        total, max_concurrent,
    )

    async def run_one(key: str, disclosure: Dict[str, Any]) -> DetectionResult:
        nonlocal completed
        result = await _detect_one_async(key, disclosure, doc, llm_config, semaphore)
        result.is_mandatory = disclosure.get(
            f"{mode}_mandatory", disclosure.get("original_mandatory", False)
        )
        result.mode = mode
        result.cross_references = disclosure.get("cross_references", {})
        completed += 1
        logger.info("[%d/%d] ✓ %s — %s", completed, total, key, result.status)
        return result

    tasks = [run_one(key, disc) for key, disc in disclosures.items()]
    results = await asyncio.gather(*tasks)
    return list(results)


def detect_all(
    schema: Dict[str, Any],
    doc: ParsedDocument,
    llm_config: LLMConfig,
    mode: str = "original",
    max_concurrent: Optional[int] = None,
) -> List[DetectionResult]:
    """
    Public synchronous entry point.
    Runs all disclosure checks concurrently via asyncio internally.
    Callers (main.py) stay synchronous — no asyncio.run() boilerplate needed there.

    Parameters
    ----------
    max_concurrent : Max parallel LLM calls. Defaults to ESG_MAX_CONCURRENT env var
                     or 6. Lower this if you hit rate limits; raise it if your API
                     tier allows more throughput.
    """
    if max_concurrent is None:
        try:
            max_concurrent = int(os.environ.get("ESG_MAX_CONCURRENT", _DEFAULT_MAX_CONCURRENT))
        except (ValueError, TypeError):
            max_concurrent = _DEFAULT_MAX_CONCURRENT

    return asyncio.run(
        _detect_all_async(
            schema=schema,
            doc=doc,
            llm_config=llm_config,
            mode=mode,
            max_concurrent=max_concurrent,
        )
    )


# ── Private helpers ────────────────────────────────────────────────────────────

def _multi_query_retrieve(
    doc: ParsedDocument,
    disclosure: Dict[str, Any],
    top_n: int = 8,
) -> List[Dict]:
    """
    Run multiple retrieval passes and merge results, deduplicating by chunk_id.

    Pass 1: Primary keywords (broad topic match)
    Pass 2: Expected data point terms (specific evidence hunt)
    Pass 3: Section identifier e.g. "E1-6", "Scope 1 emissions" (anchor match)
    Pass 4: Numeric unit hunt — queries using ESG units from the disclosure
            (e.g. "tCO2e Mt", "MWh GJ", "m3 withdrawal") to surface sparse
            data table pages that score poorly on keyword similarity but
            contain the actual numbers. This is the fix for the "GRI index
            page problem" where an appendix listing metric names outscores
            the actual data table page on keyword similarity.
    """
    seen: Dict[int, Dict] = {}  # chunk_id → best candidate

    def _add(candidates: List[Dict]) -> None:
        for c in candidates:
            cid = c["chunk_id"]
            if cid not in seen or c["score"] > seen[cid]["score"]:
                seen[cid] = c

    # Pass 1: primary keywords
    keywords = disclosure.get("keywords", [])
    if keywords:
        _add(retrieve_chunks(doc.chunks, keywords, top_n=top_n))

    # Pass 2: expected data point terms (first 3 to avoid over-querying)
    data_points = disclosure.get("expected_data_points", [])
    if data_points:
        dp_keywords = []
        for dp in data_points[:3]:
            dp_keywords.extend(dp.lower().split())
        _add(retrieve_chunks(doc.chunks, dp_keywords, top_n=top_n // 2))

    # Pass 3: section + name as anchor
    section = disclosure.get("section", "")
    name = disclosure.get("name", "")
    if section or name:
        anchor_keywords = [w for w in (section + " " + name).lower().split() if len(w) > 3]
        _add(retrieve_chunks(doc.chunks, anchor_keywords[:6], top_n=top_n // 2))

    # Pass 4: numeric unit hunt — find data table pages with actual values.
    # These pages often have sparse text (mostly numbers/symbols) so they score
    # poorly on keyword/embedding similarity despite containing the real data.
    unit_queries = disclosure.get("unit_queries", [])
    if unit_queries:
        _add(retrieve_chunks(doc.chunks, unit_queries, top_n=top_n // 2))

    # Sort by score descending, return top_n
    merged = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return merged[:top_n]

def _base_fields(key: str, d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": key,
        "section": d.get("section", ""),
        "name": d.get("name", ""),
        "category": d.get("category", ""),
        "pillar": d.get("pillar", ""),
        "is_quantitative": d.get("is_quantitative", False),
        "weight_original": d.get("weight_original", 0),
        "weight_omnibus": d.get("weight_omnibus", 0),
        "omnibus_notes": d.get("omnibus_notes", ""),
        "top_candidate_pages": [],
        "ig3": d.get("ig3", {}),
    }


def _keyword_fallback(candidates: List[Dict], disclosure: Dict) -> Dict[str, Any]:
    """Used when LLM is unavailable or JSON parsing fails completely."""
    top_score = candidates[0]["score"] if candidates else 0.0
    return {
        "status": "PARTIAL" if top_score >= 0.1 else "MISSING",
        "best_quote": candidates[0]["text"][:200] if candidates else None,
        "page": candidates[0]["page"] if candidates else None,
        "reason": f"Keyword-based detection only (LLM unavailable). Score: {top_score:.2f}",
        "quality_flags": [],
        "data_points_found": [],
        "data_points_missing": disclosure.get("expected_data_points", []),
    }
