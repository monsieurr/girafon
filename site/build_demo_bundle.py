"""
Build an anonymized static demo bundle from generated HTML reports.

Usage:
  python site/build_demo_bundle.py \
    --input-dir outputs \
    --output-dir site/demo \
    --summary outputs/summary.json

This script keeps the core product untouched and only prepares a static
showcase folder for the website.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import string
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from esg_analyzer.report.comparison import generate_comparison

LEGAL_SUFFIXES = {
    "AG", "SE", "SA", "NV", "PLC", "INC", "LTD", "LLC", "GMBH", "SAS", "SPA",
    "S.P.A.", "S.P.A", "S P A", "S R L", "SRL", "S.A.", "S.E.", "S.A.S.",
    "S.R.L.", "GROUP", "HOLDING", "HOLDINGS", "CORP", "CORPORATION", "CO",
    "COMPANY", "LIMITED", "PUBLIC", "INCORPORATED",
}


def _extract_company_name(html: str) -> str:
    match = re.search(r"<h1>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html_lib.unescape(match.group(1)).strip()


def _alias_name(index: int) -> str:
    # Company A, Company B, ... Company Z, Company AA, etc.
    letters = string.ascii_uppercase
    name = ""
    i = index
    while True:
        name = letters[i % 26] + name
        i = i // 26 - 1
        if i < 0:
            break
    return f"Company {name}"


def _strip_suffixes(name: str) -> str:
    parts = re.split(r"\s+", name.replace(",", " ").strip())
    while parts and parts[-1].upper().strip(".") in LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts).strip()


def _generate_variants(name: str) -> List[str]:
    variants = {name.strip()}
    stripped = _strip_suffixes(name)
    if stripped and stripped != name:
        variants.add(stripped)
    # Remove punctuation
    variants.add(re.sub(r"[\\.,]", " ", name).replace("  ", " ").strip())
    return [v for v in variants if v]


def _replace_terms(text: str, term_map: List[Tuple[str, str]]) -> str:
    # Replace longest terms first to avoid partial matches
    for term, alias in sorted(term_map, key=lambda x: len(x[0]), reverse=True):
        if not term:
            continue
        pattern = re.compile(rf"(?<!\\w){re.escape(term)}(?!\\w)", re.IGNORECASE)
        text = pattern.sub(alias, text)
    return text


def _select_latest_reports(paths: List[Path]) -> List[Path]:
    pattern = re.compile(r"^(?P<base>.+)_report_(?P<ts>\\d{8}_\\d{6})\\.html$")
    latest: Dict[str, Tuple[str, Path]] = {}
    for path in paths:
        match = pattern.match(path.name)
        if match:
            base = match.group("base")
            ts = match.group("ts")
        else:
            base = path.stem
            ts = ""
        if base not in latest or ts > latest[base][0]:
            latest[base] = (ts, path)
    return sorted((entry[1] for entry in latest.values()), key=lambda p: p.name)


def _anonymize_html(html: str, alias: str, term_map: List[Tuple[str, str]]) -> str:
    cleaned = html

    # Replace title and header explicitly
    cleaned = re.sub(
        r"<title>.*?</title>",
        f"<title>ESRS Gap Analysis - {alias}</title>",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<h1>.*?</h1>",
        f"<h1>{alias}</h1>",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
        count=1,
    )

    # Drop the source filename line entirely
    cleaned = re.sub(
        r"<span>\s*📄\s*Source:.*?</span>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )

    cleaned = _replace_terms(cleaned, term_map)

    # Demo badge in header
    badge_html = '<span class="mode-pill" style="background:#fde68a;color:#92400e;border-color:#f59e0b;">Demo</span>'
    cleaned = cleaned.replace(
        "<span>📋 Framework:",
        f"{badge_html}<span>📋 Framework:",
    )

    # Demo back button inside nav (only for demo outputs)
    if "nav-back" not in cleaned:
        cleaned = cleaned.replace(
            '<div class="nav-links">',
            '<div class="nav-links"><a class="nav-back" href="index.html"><- Demo Home</a>',
        )

    return cleaned


def _anonymize_summary(
    summary: List[Dict],
    name_map: Dict[str, str],
    file_map: Dict[str, str],
) -> List[Dict]:
    anonymized: List[Dict] = []
    for row in summary:
        row_copy = dict(row)
        original_company = row_copy.get("company", "")
        row_copy["company"] = name_map.get(original_company, original_company)
        original_report = row_copy.get("report_html", "")
        if original_report in file_map:
            row_copy["report_html"] = file_map[original_report]
        report_file = row_copy.get("report_file", "")
        if report_file:
            row_copy["report_file"] = "anonymized_report.pdf"
        anonymized.append(row_copy)
    return anonymized


def build_demo_bundle(
    input_dir: Path,
    output_dir: Path,
    summary_path: Path | None,
    extra_terms: List[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Clean previously generated demo files to avoid duplicates
    for pattern in ("company_*.html", "comparison.html", "summary.json", "index.html"):
        for old in output_dir.glob(pattern):
            old.unlink()

    raw_html_files = [
        p for p in input_dir.glob("*.html") if p.name not in {"comparison.html"}
    ]
    html_files = _select_latest_reports(raw_html_files)
    if not html_files:
        raise SystemExit(f"No HTML reports found in {input_dir}")

    name_map: Dict[str, str] = {}
    file_map: Dict[str, str] = {}
    demo_entries: List[Tuple[str, str]] = []

    # Extract company names first so we can build a global redaction map.
    company_names: List[str] = []
    html_cache: Dict[Path, str] = {}
    for path in html_files:
        raw_html = path.read_text(encoding="utf-8")
        html_cache[path] = raw_html
        original_name = _extract_company_name(raw_html) or path.stem
        company_names.append(original_name)

    for idx, original_name in enumerate(company_names, 1):
        name_map[original_name] = _alias_name(idx - 1)

    # Build a global redaction map (all company names + variants)
    term_map: List[Tuple[str, str]] = []
    for original_name, alias in name_map.items():
        for variant in _generate_variants(original_name):
            term_map.append((variant, alias))
    for term in extra_terms:
        term_map.append((term, "Company"))

    for idx, path in enumerate(html_files, 1):
        raw_html = html_cache[path]
        original_name = _extract_company_name(raw_html) or path.stem
        alias = name_map.get(original_name, _alias_name(idx - 1))

        out_name = f"company_{idx:02d}_report.html"
        file_map[path.name] = out_name

        anon_html = _anonymize_html(raw_html, alias, term_map)
        (output_dir / out_name).write_text(anon_html, encoding="utf-8")
        demo_entries.append((alias, out_name))

    # Anonymize summary + regenerate comparison.html if provided
    if summary_path and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        anon_summary = _anonymize_summary(summary, name_map, file_map)
        (output_dir / "summary.json").write_text(
            json.dumps(anon_summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        comparison_html = generate_comparison(anon_summary)
        (output_dir / "comparison.html").write_text(comparison_html, encoding="utf-8")
        demo_entries.insert(0, ("Comparison workspace", "comparison.html"))

    # Build simple index
    cards = "\n".join(
        f"<a class=\"demo-card\" href=\"{fname}\">"
        f"<div class=\"demo-title\">{label}</div>"
        f"<div class=\"demo-sub\">Open report</div>"
        f"</a>"
        for label, fname in demo_entries
    )
    index_html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Girafon Demo (Anonymized)</title>
  <style>
    :root {{
      --ink: #2f241b;
      --accent: #6b4d35;
      --cream: #f6efe5;
      --sand: #e6d5bf;
      --card: #ffffff;
    }}
    body {{
      font-family: "Inter", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--cream);
      color: var(--ink);
      margin: 0;
    }}
    .wrap {{
      max-width: 920px;
      margin: 0 auto;
      padding: 48px 24px 80px;
    }}
    .nav {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }}
    .brand {{
      font-family: "Satoshi", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      font-size: 20px;
      font-weight: 700;
      color: var(--ink);
      text-decoration: none;
    }}
    .nav-link {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    h1 {{
      font-family: "Satoshi", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      font-size: 36px;
      margin: 0 0 12px;
    }}
    p {{
      font-size: 16px;
      line-height: 1.6;
      margin: 0 0 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin: 24px 0 32px;
    }}
    .demo-card {{
      background: var(--card);
      border: 1px solid var(--sand);
      border-radius: 14px;
      padding: 18px;
      text-decoration: none;
      color: var(--ink);
      box-shadow: 0 6px 16px rgba(30, 24, 18, 0.08);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .demo-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 10px 22px rgba(30, 24, 18, 0.12);
    }}
    .demo-title {{
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .demo-sub {{
      font-size: 13px;
      color: #6e5a47;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--sand);
      border-radius: 16px;
      padding: 24px;
      margin-top: 20px;
    }}
    pre {{
      background: #fff;
      border: 1px solid var(--sand);
      padding: 12px;
      border-radius: 10px;
      overflow-x: auto;
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"nav\">
      <a class=\"brand\" href=\"../index.html\">Girafon</a>
      <a class=\"nav-link\" href=\"../index.html\">Back to Home</a>
    </div>
    <h1>Demo Reports (Anonymized)</h1>
    <p>These reports are generated outputs with company names removed. Use this page to
    showcase how Girafon looks and behaves without exposing real company identities.</p>
    <div class=\"grid\">
      {cards}
    </div>
    <div class=\"card\">
      <strong>Try the real tool (local)</strong>
      <p>Girafon is meant to be deployed locally. Pick a path:</p>
      <p><strong>GUI (Streamlit)</strong></p>
      <pre>pip install -r requirements.txt
ollama serve
ollama pull qwen2.5:14b
streamlit run streamlit_app.py</pre>
      <p><strong>CLI (script)</strong></p>
      <pre>python main.py --pdf path/to/report.pdf</pre>
      <p><strong>Docker (self-hosted)</strong></p>
      <pre>docker build -t girafon .
docker run -p 8501:8501 -e OLLAMA_HOST=http://host.docker.internal:11434 girafon</pre>
      <p>Source: <a class=\"nav-link\" href=\"https://github.com/monsieurr/girafon\" target=\"_blank\" rel=\"noreferrer\">github.com/monsieurr/girafon</a></p>
    </div>
  </div>
</body>
</html>"""
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build anonymized demo bundle from HTML reports.")
    parser.add_argument("--input-dir", required=True, help="Folder containing HTML reports")
    parser.add_argument("--output-dir", default="site/demo", help="Output folder for demo site")
    parser.add_argument("--summary", default="", help="Optional summary.json to anonymize")
    parser.add_argument(
        "--extra-terms",
        default="",
        help="Comma-separated list of extra terms to redact (subsidiaries, brand names)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    summary_path = Path(args.summary) if args.summary else None
    extra_terms = [t.strip() for t in args.extra_terms.split(",") if t.strip()]

    build_demo_bundle(input_dir, output_dir, summary_path, extra_terms)
    print(f"Demo bundle created in: {output_dir}")
    print("Review the anonymized HTML for any remaining company references before publishing.")
    print("Publish only the site/demo folder (not outputs/).")


if __name__ == "__main__":
    main()
