#!/usr/bin/env python3
"""
main.py — ESRS Gap Detector CLI

Usage:
  python main.py --pdf report.pdf
  python main.py --pdf report.pdf --company "Acme Corp" --mode omnibus
  python main.py --pdf report.pdf --provider ollama --model llama3.2
  python main.py --providers   (list all supported LLM providers)
  python main.py --check       (test your LLM connection)
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from esg_analyzer.utils.names import clean_company_name

load_dotenv()

logger = logging.getLogger("esg_detector")


def _setup_logging() -> None:
    """
    Clean, screenshot-friendly logs by default.
    Only warnings and errors are shown unless explicitly printed.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)
    logging.getLogger("fitz").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    for _litellm_logger in (
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
        "litellm",
        "litellm.utils",
        "litellm.main",
    ):
        logging.getLogger(_litellm_logger).setLevel(logging.WARNING)


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(
        description="ESRS Gap Detector — Analyse ESG reports for CSRD compliance gaps",
    )
    parser.add_argument("--pdf",       required=False, help="Path to ESG report (.pdf or .html)")
    parser.add_argument("--input-dir", default="", help="Batch mode: folder containing PDF reports")
    parser.add_argument("--output-dir", default="", help="Batch mode: output folder for reports and summary")
    parser.add_argument("--diff-base", default="", help="Diff mode: baseline PDF report")
    parser.add_argument("--diff-new", default="", help="Diff mode: comparison PDF report")
    parser.add_argument("--diff-output", default="", help="Diff mode: output HTML path for diff report")
    parser.add_argument("--diff-base-label", default="Baseline", help="Diff mode: label for baseline report")
    parser.add_argument("--diff-new-label", default="Comparison", help="Diff mode: label for comparison report")
    parser.add_argument("--company",   default="",     help="Company name for the report header")
    parser.add_argument("--output",    default="",     help="Output HTML path")
    parser.add_argument("--json",      default="",     help="Also save raw results as JSON")
    parser.add_argument(
        "--mode", choices=["original", "omnibus"], default="original",
        help="ESRS mode: original (2023) or omnibus (2026 simplified)",
    )
    parser.add_argument("--provider",  default="", help="LLM provider: anthropic, openai, ollama, groq, mistral")
    parser.add_argument("--model",     default="", help="Model name (e.g. llama3.2, gpt-4o-mini)")
    parser.add_argument("--concurrent", type=int, default=None,
                        help="Max concurrent LLM calls (default: 6, lower if you hit rate limits)")
    parser.add_argument("--chunk-words", type=int, default=500,
                        help="Chunk size in words (default: 500)")
    parser.add_argument("--overlap-words", type=int, default=120,
                        help="Overlap between chunks in words (default: 120)")
    parser.add_argument("--min-chunk-words", type=int, default=40,
                        help="Discard chunks shorter than this (default: 40)")
    parser.add_argument("--schema", choices=["basic", "ig3-core", "ig3"], default="basic",
                        help="Schema profile: basic (20 disclosures), ig3-core (ESRS2+E1+G1), or ig3 (full datapoints)")
    parser.add_argument("--taxonomy-map", default="",
                        help="Path to ESRS taxonomy mapping JSON (optional)")
    parser.add_argument("--providers", action="store_true", help="List supported LLM providers and exit")
    parser.add_argument("--check",     action="store_true", help="Test your LLM connection and exit")
    args = parser.parse_args()

    if args.providers:
        from esg_analyzer.llm_provider import LLMConfig
        print(LLMConfig.list_providers())
        sys.exit(0)

    if args.check:
        _run_check(args)
        sys.exit(0)

    diff_mode = bool(args.diff_base and args.diff_new)
    if diff_mode and (args.pdf or args.input_dir):
        parser.error("Diff mode cannot be combined with --pdf or --input-dir.")

    if not args.pdf and not args.input_dir and not diff_mode:
        parser.error("--pdf, --input-dir, or --diff-base/--diff-new is required (try --check to test your LLM)")

    batch_mode = bool(args.input_dir)

    doc_path = None
    if not batch_mode and not diff_mode:
        doc_path = Path(args.pdf) if args.pdf else None
        if not doc_path or not doc_path.exists():
            logger.error("File not found: %s", doc_path)
            sys.exit(1)

    def _stamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    if diff_mode:
        base_path = Path(args.diff_base)
        new_path = Path(args.diff_new)
        if not base_path.exists() or not new_path.exists():
            logger.error("Diff report file not found: %s or %s", base_path, new_path)
            sys.exit(1)
        diff_stamp = _stamp()
        print(f"\n{'='*56}")
        print(f"  ESRS Gap Detector — Diff")
        print(f"  Baseline : {base_path.name}")
        print(f"  Compare  : {new_path.name}")
        print(f"  Mode     : {args.mode.upper()}")
        print(f"  Schema   : {args.schema}")
        print(f"{'='*56}\n")
    elif not batch_mode:
        company_name = args.company or clean_company_name(doc_path.name)
        out_stamp = _stamp()
        output_path = args.output or str(doc_path.with_suffix("")) + f"_report_{out_stamp}.html"

        print(f"\n{'='*56}")
        print(f"  ESRS Gap Detector")
        print(f"  Company  : {company_name}")
        print(f"  Mode     : {args.mode.upper()}")
        print(f"  Source   : {doc_path.name}")
        print(f"  Schema   : {args.schema}")
        print(f"{'='*56}\n")
    else:
        input_dir = Path(args.input_dir)
        output_dir = Path(args.output_dir) if args.output_dir else (input_dir / "girafon_out")
        print(f"\n{'='*56}")
        print(f"  ESRS Gap Detector — Batch")
        print(f"  Input    : {input_dir}")
        print(f"  Output   : {output_dir}")
        print(f"  Mode     : {args.mode.upper()}")
        print(f"  Schema   : {args.schema}")
        print(f"{'='*56}\n")

    if batch_mode:
        if args.provider or args.model:
            print("  NOTE     : Batch mode forces Ollama / qwen2.5:14b (overriding provider/model).")
        args.provider = "ollama"
        args.model = "qwen2.5:14b"

    # ── LLM config ─────────────────────────────────────────────────────────────
    from esg_analyzer.llm_provider import LLMConfig
    try:
        llm_config = LLMConfig(provider=args.provider or None, model=args.model or None)
    except ValueError as e:
        logger.error("LLM config error: %s", e)
        sys.exit(1)
    print(f"  LLM      : {llm_config.provider} / {llm_config.model}")
    from esg_analyzer.pipeline import default_concurrency, run_pipeline
    from esg_analyzer.batch import analyze_batch
    from esg_analyzer.diff import compute_diff_report
    from esg_analyzer.report.diff_report import generate_diff_report
    import os as _os
    concurrent = args.concurrent or default_concurrency(llm_config)
    source = "CLI" if args.concurrent else ("env" if _os.environ.get("ESG_MAX_CONCURRENT") else "auto")
    print(f"  Workers  : {concurrent} concurrent LLM calls ({source})")
    print(f"  Chunking : {args.chunk_words} words, overlap {args.overlap_words} words\n")
    if args.schema == "ig3":
        print("  NOTE     : IG3 mode runs 1,000+ datapoints and can take a long time.")
        print("             Consider using a local model with low concurrency.\n")
    if args.schema == "ig3-core":
        print("  NOTE     : IG3-core runs ESRS 2 + E1 + G1 (faster than full IG3).")
        print("             Use this for a high-impact quick scan.\n")

    def _progress(msg: str) -> None:
        print(msg)

    def _warn(msg: str) -> None:
        print(msg)

    frameworks_dir = Path(__file__).parent / "esg_analyzer" / "frameworks"
    schema_path = frameworks_dir / ("esrs_schema.json" if args.schema == "basic" else "esrs_ig3_schema.json")
    default_taxonomy_map = frameworks_dir / "esrs_taxonomy_map.json"
    taxonomy_map_path = None
    if args.schema == "basic":
        taxonomy_map_path = Path(args.taxonomy_map) if args.taxonomy_map else (
            default_taxonomy_map if default_taxonomy_map.exists() else None
        )
    ig3_scope = None
    if args.schema == "ig3-core":
        ig3_scope = {"ESRS 2", "ESRS 2 MDR", "E1", "G1"}

    if diff_mode:
        base_path = Path(args.diff_base)
        new_path = Path(args.diff_new)
        base_name = args.diff_base_label or "Baseline"
        new_name = args.diff_new_label or "Comparison"
        diff_output = (
            Path(args.diff_output)
            if args.diff_output
            else (base_path.parent / f"{base_path.stem}_vs_{new_path.stem}_diff_{diff_stamp}.html")
        )

        try:
            base_result = run_pipeline(
                doc_path=base_path,
                company_name=base_name,
                mode=args.mode,
                llm_config=llm_config,
                schema_path=schema_path,
                taxonomy_map_path=taxonomy_map_path,
                ig3_scope=ig3_scope,
                schema_profile=args.schema,
                output_path=str(base_path.with_suffix("")) + f"_report_{diff_stamp}.html",
                chunk_words=args.chunk_words,
                overlap_words=args.overlap_words,
                min_chunk_words=args.min_chunk_words,
                max_concurrent=concurrent,
                progress=_progress,
                warn=_warn,
            )
            new_result = run_pipeline(
                doc_path=new_path,
                company_name=new_name,
                mode=args.mode,
                llm_config=llm_config,
                schema_path=schema_path,
                taxonomy_map_path=taxonomy_map_path,
                ig3_scope=ig3_scope,
                schema_profile=args.schema,
                output_path=str(new_path.with_suffix("")) + f"_report_{diff_stamp}.html",
                chunk_words=args.chunk_words,
                overlap_words=args.overlap_words,
                min_chunk_words=args.min_chunk_words,
                max_concurrent=concurrent,
                progress=_progress,
                warn=_warn,
            )
        except Exception as e:
            logger.error("Diff analysis failed: %s", e)
            sys.exit(1)

        diff_report = compute_diff_report(
            base_result.score_report,
            new_result.score_report,
            base_label=base_name,
            new_label=new_name,
            base_report_html=Path(base_result.output_path).name,
            new_report_html=Path(new_result.output_path).name,
        )
        generate_diff_report(diff_report, output_path=str(diff_output))
        print(f"\n  Diff report → {diff_output}")
        print(f"{'='*56}\n")
        return

    if batch_mode:
        try:
            analyze_batch(
                input_dir=input_dir,
                output_dir=output_dir,
                llm_config=llm_config,
                schema_path=schema_path,
                taxonomy_map_path=taxonomy_map_path,
                ig3_scope=ig3_scope,
                schema_profile=args.schema,
                mode=args.mode,
                chunk_words=args.chunk_words,
                overlap_words=args.overlap_words,
                min_chunk_words=args.min_chunk_words,
                max_concurrent=concurrent,
                progress=_progress,
                warn=_warn,
            )
        except Exception as e:
            logger.error("Batch analysis failed: %s", e)
            sys.exit(1)
        print(f"\n  Output → {output_dir}")
        print(f"{'='*56}\n")
        return

    try:
        pipeline_result = run_pipeline(
            doc_path=doc_path,
            company_name=company_name,
            mode=args.mode,
            llm_config=llm_config,
            schema_path=schema_path,
            taxonomy_map_path=taxonomy_map_path,
            ig3_scope=ig3_scope,
            schema_profile=args.schema,
            output_path=output_path,
            chunk_words=args.chunk_words,
            overlap_words=args.overlap_words,
            min_chunk_words=args.min_chunk_words,
            max_concurrent=concurrent,
            progress=_progress,
            warn=_warn,
        )
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Pipeline failed: %s", e)
        sys.exit(1)

    score_report = pipeline_result.score_report

    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(score_report, f, indent=2, ensure_ascii=False)
            print(f"OK        JSON saved: {args.json}")
        except OSError as e:
            logger.warning("Could not write JSON output to '%s': %s", args.json, e)

    # ── Print summary ──────────────────────────────────────────────────────────
    s = score_report
    print(f"\n{'='*56}")
    print(f"  Score     : {s['overall_score']}/100 — {s['band']['label']}")
    print(f"  Compliance: {s['compliance_rate']}% mandatory")
    print(f"  Found / Partial / Missing: {s['found_count']} / {s['partial_count']} / {s['missing_count']}")
    print(f"\n  Category breakdown:")
    for cat, cs in s["category_scores"].items():
        bar = "█" * int(cs["score"] / 10) + "░" * (10 - int(cs["score"] / 10))
        print(f"    {cat:<14} {bar} {cs['score']}")
    print(f"\n  Top recommendations:")
    for i, rec in enumerate(s["recommendations"][:3], 1):
        print(f"    {i}. [{rec['priority']}] {rec['action'][:75]}")
    print(f"\n  Report → {output_path}")
    print(f"{'='*56}\n")


# ── Connection check ───────────────────────────────────────────────────────────

def _run_check(args) -> None:
    """
    Test the LLM connection with a trivial prompt.
    Usage: python main.py --check
           python main.py --check --provider ollama --model llama3.2
    """
    from esg_analyzer.llm_provider import LLMConfig, LLMError, call_llm

    print("\n── LLM Connection Check ──────────────────────────────")

    try:
        config = LLMConfig(provider=args.provider or None, model=args.model or None)
    except ValueError as e:
        print(f"  ✗ Config error: {e}")
        return

    print(f"  Provider : {config.provider}")
    print(f"  Model    : {config.model}")

    if config.provider == "ollama":
        print(f"  Endpoint : http://localhost:11434 (local)")
        print(f"\n  Tip: make sure Ollama is running → ollama serve")
        print(f"  Tip: pull your model first      → ollama pull {config.model}")
    else:
        print(f"  Mode     : cloud API (key from environment)")

    print("\n  Sending test prompt…", end=" ", flush=True)

    try:
        response = call_llm(
            system_prompt="You are a helpful assistant. Reply in JSON only.",
            user_prompt='Reply with exactly: {"status": "ok"}',
            config=config,
        )
        if "ok" in response.lower():
            print("✓")
            print(f"\n  ✅ Connection successful — {config.provider}/{config.model} is ready.")
        else:
            print("⚠")
            print(f"\n  ⚠  Connected but unexpected response: {response[:100]}")
            print("     The model may not follow JSON instructions well.")
            print("     Try a different model (e.g. ollama pull mistral).")
    except LLMError as e:
        print("✗")
        print(f"\n  ✗ Connection failed:\n  {e}")
        if config.provider == "ollama":
            print("\n  To fix:")
            print("    1. Start Ollama:        ollama serve")
            print(f"   2. Pull the model:      ollama pull {config.model}")
            print("    3. Re-run this check:   python main.py --check --provider ollama")

    print("─" * 52 + "\n")


if __name__ == "__main__":
    main()
