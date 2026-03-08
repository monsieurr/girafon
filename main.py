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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("fitz").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
for _litellm_logger in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm", "litellm.utils", "litellm.main"):
    logging.getLogger(_litellm_logger).setLevel(logging.WARNING)

logger = logging.getLogger("esg_detector")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESRS Gap Detector — Analyse ESG reports for CSRD compliance gaps",
    )
    parser.add_argument("--pdf",       required=False, help="Path to ESG report (.pdf or .html)")
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

    if not args.pdf:
        parser.error("--pdf is required  (try --check to test your LLM, --providers to list options)")

    doc_path = Path(args.pdf)
    if not doc_path.exists():
        logger.error("File not found: %s", doc_path)
        sys.exit(1)

    company_name = args.company or doc_path.stem.replace("_", " ").title()
    output_path = args.output or str(doc_path.with_suffix("")) + "_report.html"

    print(f"\n{'='*56}")
    print(f"  ESRS Gap Detector")
    print(f"  Company  : {company_name}")
    print(f"  Mode     : {args.mode.upper()}")
    print(f"  Source   : {doc_path.name}")
    print(f"{'='*56}\n")

    # ── LLM config ─────────────────────────────────────────────────────────────
    from esg_analyzer.llm_provider import LLMConfig
    try:
        llm_config = LLMConfig(provider=args.provider or None, model=args.model or None)
    except ValueError as e:
        logger.error("LLM config error: %s", e)
        sys.exit(1)
    print(f"  LLM      : {llm_config.provider} / {llm_config.model}")
    # --concurrent > ESG_MAX_CONCURRENT env var > provider-aware smart default
    # (Ollama = 1, cloud APIs = 6 — avoids connection timeouts on local models)
    import os as _os
    concurrent = (
        args.concurrent
        or int(_os.environ.get("ESG_MAX_CONCURRENT", 0))
        or llm_config.recommended_concurrency
    )
    source = "CLI" if args.concurrent else ("env" if _os.environ.get("ESG_MAX_CONCURRENT") else "auto")
    print(f"  Workers  : {concurrent} concurrent LLM calls ({source})\n")

    # ── Parse document ─────────────────────────────────────────────────────────
    print("📄 [1/4] Parsing document…")
    from esg_analyzer.parsers.document_parser import ParseError, UnsupportedFormatError, parse_document
    try:
        doc = parse_document(doc_path)
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(1)
    except (UnsupportedFormatError, ParseError) as e:
        logger.error("%s", e)
        sys.exit(1)
    print(f"       ✓ {doc.total_pages} pages · {doc.total_chunks} chunks · method: {doc.extraction_method}")

    if doc.extraction_method == "pdfplumber":
        print()
        print("  ⚠️  PDF QUALITY WARNING")
        print("     pymupdf4llm is not installed — using pdfplumber fallback.")
        print("     Multi-column tables (GHG data, social metrics) may be garbled,")
        print("     which can cause FOUND disclosures to be reported as PARTIAL.")
        print("     For best results: pip install pymupdf4llm")
        print()
    else:
        print()

    # ── Load schema ────────────────────────────────────────────────────────────
    print("📋 [2/4] Loading ESRS schema…")
    schema_path = Path(__file__).parent / "esg_analyzer" / "frameworks" / "esrs_schema.json"
    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        logger.error(
            "ESRS schema not found at: %s\n"
            "Ensure esrs_schema.json is present in esg_analyzer/frameworks/",
            schema_path,
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error("ESRS schema is not valid JSON: %s", e)
        sys.exit(1)

    scoring_config = schema.get("_scoring_config", {})
    n = sum(1 for k in schema if not k.startswith("_"))
    if n == 0:
        logger.error("ESRS schema contains no disclosures (all keys start with '_'). Check the file.")
        sys.exit(1)
    print(f"       ✓ {n} disclosures (mode: {args.mode})\n")

    # ── Detect ─────────────────────────────────────────────────────────────────
    print(f"🔍 [3/4] Detecting disclosures ({n} checks, {concurrent} concurrent)…")
    from esg_analyzer.analysis.detector import detect_all
    try:
        results = detect_all(
            schema=schema,
            doc=doc,
            llm_config=llm_config,
            mode=args.mode,
            max_concurrent=concurrent,
        )
    except Exception as e:
        logger.error("Detection failed unexpectedly: %s", e, exc_info=True)
        sys.exit(1)
    print()

    # ── Score + report ─────────────────────────────────────────────────────────
    print("📊 [4/4] Scoring and generating report…")
    from esg_analyzer.analysis.scorer import compute_scores
    from esg_analyzer.report.generator import generate_report

    try:
        score_report = compute_scores(
            results=[vars(r) for r in results],
            scoring_config=scoring_config,
            mode=args.mode,
        )
    except Exception as e:
        logger.error("Scoring failed: %s", e, exc_info=True)
        sys.exit(1)

    try:
        generate_report(
            score_report=score_report,
            company_name=company_name,
            pdf_filename=doc_path.name,
            output_path=output_path,
            mode=args.mode,
        )
    except OSError as e:
        logger.error("Could not write report to '%s': %s", output_path, e)
        sys.exit(1)

    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(score_report, f, indent=2, ensure_ascii=False)
            print(f"       ✓ JSON saved: {args.json}")
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
