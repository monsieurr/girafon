# Benchmarks

This folder contains a curated list of recent ESG/CSRD/ESRS reports you can
use to build an evaluation dataset. The list prioritizes EU issuers and
reports that explicitly mention ESRS/CSRD alignment.

Files:
- `reports_2024_2025.csv` — 30 report sources with URLs and a quick ESRS/CSRD status flag.
- `reports_2024_2025_split.csv` — Same list with train/val/test split column.
- `download_reports.py` — Best-effort downloader for PDFs (some sites may require login).
- `make_split.py` — Create a reproducible train/val/test split.

Notes:
- Many sources are hosted on sustainabilityreports.com; some downloads may require a free account.
- Treat `esrs_csrd_status=partial` or `unknown` as a cue to manually verify alignment.

Usage:

1) Download PDFs
```bash
python benchmarks/download_reports.py --csv benchmarks/reports_2024_2025.csv --out-dir benchmarks/pdfs
```

2) Create split
```bash
python benchmarks/make_split.py --csv benchmarks/reports_2024_2025.csv --out benchmarks/reports_2024_2025_split.csv
```
