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
from pathlib import Path
from typing import Dict, List, Tuple

from esg_analyzer.report.comparison import generate_comparison


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


def _anonymize_html(html: str, original_name: str, alias: str, extra_terms: List[str]) -> str:
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

    # Replace occurrences of company name + optional extra terms
    terms = [t for t in [original_name, *extra_terms] if t]
    for term in terms:
        cleaned = re.sub(re.escape(term), alias, cleaned, flags=re.IGNORECASE)

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

    html_files = sorted(
        p for p in input_dir.glob("*.html") if p.name not in {"comparison.html"}
    )
    if not html_files:
        raise SystemExit(f"No HTML reports found in {input_dir}")

    name_map: Dict[str, str] = {}
    file_map: Dict[str, str] = {}
    demo_entries: List[Tuple[str, str]] = []

    for idx, path in enumerate(html_files, 1):
        raw_html = path.read_text(encoding="utf-8")
        original_name = _extract_company_name(raw_html) or path.stem
        alias = _alias_name(idx - 1)
        name_map[original_name] = alias

        out_name = f"company_{idx:02d}_report.html"
        file_map[path.name] = out_name

        anon_html = _anonymize_html(raw_html, original_name, alias, extra_terms)
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
    links = "\n".join(
        f"<li><a href=\"{fname}\">{label}</a></li>" for label, fname in demo_entries
    )
    index_html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Girafon Demo (Anonymized)</title>
  <style>
    body {{ font-family: Inter, system-ui, sans-serif; background: #f6f2ed; color: #24180f; }}
    .wrap {{ max-width: 800px; margin: 0 auto; padding: 48px 20px; }}
    h1 {{ font-size: 28px; }}
    p {{ line-height: 1.6; }}
    ul {{ padding-left: 18px; }}
    li {{ margin: 8px 0; }}
    a {{ color: #a1581f; font-weight: 600; text-decoration: none; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>Girafon Demo (Anonymized)</h1>
    <p>These reports are generated outputs with company names removed. Use this page to
    showcase how Girafon looks and behaves without exposing real company identities.</p>
    <ul>
      {links}
    </ul>
    <h2 style=\"margin-top:32px;\">Try the real tool (local)</h2>
    <p>Girafon is meant to be deployed locally. Quick start:</p>
    <pre style=\"background:#fff;border:1px solid #e6d5bf;padding:12px;border-radius:10px;\">pip install -r requirements.txt
ollama serve
ollama pull qwen2.5:14b
streamlit run streamlit_app.py</pre>
    <p>CLI mode:</p>
    <pre style=\"background:#fff;border:1px solid #e6d5bf;padding:12px;border-radius:10px;\">python main.py --pdf path/to/report.pdf</pre>
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


if __name__ == "__main__":
    main()
