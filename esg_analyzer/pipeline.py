"""
pipeline.py
-----------
Shared orchestration for CLI and Streamlit UI.
Keeps the core flow in one place (KISS + DRY):
  parse -> load schema -> detect -> score -> generate report
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from esg_analyzer.analysis.detector import DetectionResult, detect_all
from esg_analyzer.analysis.materiality import detect_materiality
from esg_analyzer.analysis.scorer import compute_scores
from esg_analyzer.llm_provider import LLMConfig
from esg_analyzer.parsers.document_parser import ParsedDocument, parse_document
from esg_analyzer.report.generator import generate_report
from esg_analyzer.taxonomy.mapping import load_taxonomy_map


@dataclass
class PipelineResult:
    doc: ParsedDocument
    results: List[DetectionResult]
    score_report: Dict[str, Any]
    output_path: str
    schema: Dict[str, Any]
    taxonomy_map: Dict[str, Any] | None = None
    materiality_map: Dict[str, Any] | None = None


def load_schema(schema_path: Path) -> Dict[str, Any]:
    if not schema_path.exists():
        raise FileNotFoundError(f"ESRS schema not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    if not any(k for k in schema.keys() if not k.startswith("_")):
        raise ValueError("ESRS schema contains no disclosures (all keys start with '_').")
    return schema


def default_concurrency(llm_config: LLMConfig) -> int:
    """
    Local-first default concurrency.
    - Default: 1 (safe for local LLMs)
    - ESG_MAX_CONCURRENT env var overrides all defaults if set
    """
    env_max = int(os.environ.get("ESG_MAX_CONCURRENT", "0") or 0)
    if env_max > 0:
        return env_max
    return 1


def run_pipeline(
    *,
    doc_path: Path,
    company_name: str,
    mode: str,
    llm_config: LLMConfig,
    schema_path: Path,
    taxonomy_map_path: Path | None = None,
    ig3_scope: set[str] | None = None,
    output_path: str,
    chunk_words: int = 500,
    overlap_words: int = 120,
    min_chunk_words: int = 40,
    max_concurrent: int = 1,
    progress: Optional[Callable[[str], None]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> PipelineResult:
    def _notify(cb: Optional[Callable[[str], None]], msg: str) -> None:
        if cb:
            cb(msg)

    _notify(progress, "Step 1/4  Parsing document...")
    doc = parse_document(
        doc_path,
        chunk_words=chunk_words,
        overlap_words=overlap_words,
        min_chunk_words=min_chunk_words,
    )
    _notify(
        progress,
        f"OK        {doc.total_pages} pages / {doc.total_chunks} chunks / method: {doc.extraction_method}",
    )
    if doc.extraction_method == "pdfplumber":
        _notify(
            warn,
            "WARNING   pymupdf4llm not installed - table-heavy reports may be garbled. "
            "Install pymupdf4llm for best accuracy.",
        )

    _notify(progress, "Step 2/4  Loading ESRS schema...")
    schema = load_schema(schema_path)
    if ig3_scope:
        schema = _filter_schema_by_esrs(schema, ig3_scope)
    n = sum(1 for k in schema if not k.startswith("_"))
    scope_note = f" [scope: {', '.join(sorted(ig3_scope))}]" if ig3_scope else ""
    _notify(progress, f"OK        {n} disclosures (mode: {mode}){scope_note}")

    taxonomy_map = load_taxonomy_map(taxonomy_map_path)

    _notify(progress, "Step 2b/4 Materiality scan (strict)...")
    materiality_map = detect_materiality(doc)

    _notify(progress, f"Step 3/4  Detecting disclosures ({n} checks, {max_concurrent} concurrent)...")
    results = detect_all(
        schema=schema,
        doc=doc,
        llm_config=llm_config,
        mode=mode,
        max_concurrent=max_concurrent,
    )

    _notify(progress, "Step 4/4  Scoring and generating report...")
    score_report = compute_scores(
        results=[vars(r) for r in results],
        scoring_config=schema.get("_scoring_config", {}),
        taxonomy_map=taxonomy_map,
        materiality_map=materiality_map,
        mode=mode,
    )
    generate_report(
        score_report=score_report,
        company_name=company_name,
        pdf_filename=Path(doc_path).name,
        output_path=output_path,
        mode=mode,
    )

    return PipelineResult(
        doc=doc,
        results=results,
        score_report=score_report,
        output_path=output_path,
        schema=schema,
        taxonomy_map=taxonomy_map,
        materiality_map=materiality_map,
    )


def _filter_schema_by_esrs(schema: Dict[str, Any], allowed: set[str]) -> Dict[str, Any]:
    filtered: Dict[str, Any] = {}
    for k, v in schema.items():
        if k.startswith("_"):
            filtered[k] = v
            continue
        esrs = (v.get("ig3", {}) or {}).get("esrs", "")
        if esrs in allowed:
            filtered[k] = v
    return filtered
