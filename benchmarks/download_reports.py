#!/usr/bin/env python3
"""
Best-effort downloader for the benchmark report list.
Supports direct PDF links and HTML landing pages with PDF links.
Some sources may require login or block automated downloads.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


PDF_RE = re.compile(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', re.IGNORECASE)


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
    return cleaned or "report"


def _fetch(url: str, timeout: int = 20) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": "GirafonBench/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        return data, content_type


def _pick_best_pdf_link(base_url: str, html: str, year: str) -> Optional[str]:
    links = PDF_RE.findall(html)
    if not links:
        return None

    def _score(link: str) -> int:
        score = 0
        l = link.lower()
        for kw in ("sustainability", "esrs", "csrd", "statement", "report", "annual", "non-financial"):
            if kw in l:
                score += 2
        if year and year in l:
            score += 3
        if "download" in l:
            score += 1
        return score

    links = [urljoin(base_url, l) for l in links]
    links.sort(key=_score, reverse=True)
    return links[0]


def _is_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def download_reports(csv_path: Path, out_dir: Path, sleep_s: float, limit: int, overwrite: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir.parent / "download_log.csv"

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if limit > 0:
        rows = rows[:limit]

    log_exists = log_path.exists()
    with open(log_path, "a", encoding="utf-8", newline="") as logf:
        writer = csv.writer(logf)
        if not log_exists:
            writer.writerow(["company", "year", "source_url", "status", "saved_as", "notes"])

        for i, row in enumerate(rows, 1):
            company = row.get("company", "unknown")
            year = row.get("year", "")
            source_url = row.get("source_url", "")
            if not source_url:
                writer.writerow([company, year, source_url, "skip", "", "missing source_url"])
                continue

            safe_name = _sanitize_filename(f"{company}_{year}")
            out_path = out_dir / f"{safe_name}.pdf"

            if out_path.exists() and not overwrite:
                writer.writerow([company, year, source_url, "skip", out_path.name, "already exists"])
                continue

            try:
                if _is_pdf_url(source_url):
                    data, ctype = _fetch(source_url)
                    if b"%PDF" not in data[:1024] and "pdf" not in ctype.lower():
                        writer.writerow([company, year, source_url, "error", "", "not a PDF response"])
                        continue
                    out_path.write_bytes(data)
                    writer.writerow([company, year, source_url, "ok", out_path.name, "direct pdf"])
                else:
                    html_bytes, _ = _fetch(source_url)
                    html = html_bytes.decode("utf-8", errors="ignore")
                    pdf_url = _pick_best_pdf_link(source_url, html, year)
                    if not pdf_url:
                        writer.writerow([company, year, source_url, "manual", "", "no PDF link found"])
                        continue
                    data, ctype = _fetch(pdf_url)
                    if b"%PDF" not in data[:1024] and "pdf" not in ctype.lower():
                        writer.writerow([company, year, pdf_url, "error", "", "linked file not PDF"])
                        continue
                    out_path.write_bytes(data)
                    writer.writerow([company, year, pdf_url, "ok", out_path.name, "resolved from landing page"])
            except Exception as e:
                writer.writerow([company, year, source_url, "error", "", f"{type(e).__name__}: {e}"])

            time.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download benchmark ESG reports (best effort).")
    parser.add_argument("--csv", default="benchmarks/reports_2024_2025.csv", help="CSV list of reports")
    parser.add_argument("--out-dir", default="benchmarks/pdfs", help="Output directory for PDFs")
    parser.add_argument("--sleep", type=float, default=1.5, help="Seconds to wait between requests")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of downloads (0 = all)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    out_dir = Path(args.out_dir)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    download_reports(csv_path, out_dir, sleep_s=args.sleep, limit=args.limit, overwrite=args.overwrite)
    print(f"Done. PDFs saved to: {out_dir}")


if __name__ == "__main__":
    main()
