"""
batch.py
--------
Batch analysis helper:
Input directory of PDFs → individual HTML reports + summary.json + comparison.html

This reuses the existing pipeline without changing the single-report flow.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from esg_analyzer.llm_provider import LLMConfig
from esg_analyzer.pipeline import run_pipeline
from esg_analyzer.report.comparison import generate_comparison
from esg_analyzer.utils.names import clean_company_name


def analyze_batch(
    *,
    input_dir: Path,
    output_dir: Path,
    llm_config: LLMConfig,
    schema_path: Path,
    taxonomy_map_path: Path | None,
    ig3_scope: set[str] | None,
    schema_profile: str | None = None,
    mode: str,
    chunk_words: int,
    overlap_words: int,
    min_chunk_words: int,
    max_concurrent: int,
    progress: Optional[Callable[[str], None]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Analyze every PDF in input_dir and write outputs into output_dir.
    Returns the summary list used to generate summary.json and comparison.html.
    """
    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise ValueError(f"No PDFs found in: {input_dir}")

    summary: List[Dict[str, Any]] = []
    batch_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _notify(cb: Optional[Callable[[str], None]], msg: str) -> None:
        if cb:
            cb(msg)

    for idx, pdf in enumerate(pdfs, 1):
        company_name = clean_company_name(pdf.name)
        report_path = output_dir / f"{pdf.stem}_report_{batch_stamp}.html"
        _notify(progress, f"[{idx}/{len(pdfs)}] {company_name} — {pdf.name}")

        try:
            result = run_pipeline(
                doc_path=pdf,
                company_name=company_name,
                mode=mode,
                llm_config=llm_config,
                schema_path=schema_path,
                taxonomy_map_path=taxonomy_map_path,
                ig3_scope=ig3_scope,
                schema_profile=schema_profile,
                output_path=str(report_path),
                chunk_words=chunk_words,
                overlap_words=overlap_words,
                min_chunk_words=min_chunk_words,
                max_concurrent=max_concurrent,
                progress=progress,
                warn=warn,
            )

            score = result.score_report
            high_priority_gaps = sum(
                1 for rec in score.get("recommendations", []) if rec.get("priority") == "HIGH"
            )
            summary.append(
                {
                    "company": company_name,
                    "report_file": pdf.name,
                    "report_html": report_path.name,
                    "overall_score": score.get("overall_score"),
                    "mandatory_compliance": score.get("compliance_rate"),
                    "mandatory_missing": score.get("mandatory_missing"),
                    "found": score.get("found_count"),
                    "partial": score.get("partial_count"),
                    "missing": score.get("missing_count"),
                    "high_priority_gaps": high_priority_gaps,
                    "greenwashing_flags": len(score.get("quality_flags_summary", [])),
                }
            )
        except Exception as exc:
            err = str(exc)
            _notify(warn, f"[{idx}/{len(pdfs)}] ERROR — {pdf.name}: {err}")
            summary.append(
                {
                    "company": company_name,
                    "report_file": pdf.name,
                    "report_html": "",
                    "overall_score": None,
                    "mandatory_compliance": None,
                    "mandatory_missing": 0,
                    "found": 0,
                    "partial": 0,
                    "missing": 0,
                    "high_priority_gaps": 0,
                    "greenwashing_flags": 0,
                    "status": "failed",
                    "error": err,
                }
            )

    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    comparison_path = output_dir / "comparison.html"
    generate_comparison(summary, output_path=str(comparison_path))

    _notify(progress, f"Batch complete. summary.json + comparison.html saved to {output_dir}")
    return summary
