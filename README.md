# 🦒 Project Girafon

**Automatically analyse any ESG/sustainability report against ESRS (CSRD) requirements and find exactly what's missing — with evidence.**


**DISCLAIMER** : 
This isn't a mature tool by any mean. It can indeed give you hinsights on ESG/sustainability reports but please do not use its results in its current version (or, use with caution please). Besides, if you see incoherent results please tell me and I will gladly investigate.



---

Upload any company ESG/sustainability PDF and get:

-  **Per-disclosure status** — FOUND / PARTIAL / MISSING for each ESRS section
-  **Evidence quotes** — the exact sentence and page number that proves each finding
-  **Weighted score** — 0–100 overall score with E / S / G breakdown
-  **Greenwashing flags** — vague language, missing baselines, unsupported net-zero claims
-  **Actionable recommendations** — prioritised list of what to fix and what to add
-  **Omnibus mode** — toggle between original ESRS 2023 and the 2026 simplified post-Omnibus framework

---

## Why this exists

CSRD (Corporate Sustainability Reporting Directive) requires thousands of EU companies to publish detailed ESG reports aligned with ESRS standards. Most companies and consultancies spend weeks manually checking compliance. This tool does it in minutes.

**Key differentiators vs other tools:**
- Framework-aware, not generic: checks against specific ESRS sections (E1-6, S1-14, G1-3, etc.)
- Evidence-backed: every finding cites the source passage, page, and quote
- Omnibus-aware: knows which disclosures changed in the February 2026 EU Omnibus reform
- Open source: free, auditable, no vendor lock-in

---

## Installation

```bash
# 1. Clone
git clone https://github.com/monsieurr/girafon
cd girafon

# 2. Install dependencies
pip install -r requirements.txt
```

---

## Usage (two ways)

You can use Girafon either:
- **From the terminal (CLI)** for batch runs and automation
- **With a GUI (Streamlit)** for demos and quick uploads

**Choose your path**

| Use case | Best choice | Why |
|---------|-------------|-----|
| Batch runs, automation, CI | **CLI (Terminal)** | Fast, scriptable, easy to scale |
| Demos, non-technical users | **GUI (Streamlit)** | Upload + click, shareable |

### CLI — Terminal usage

#### Option A — Local LLM with Ollama (no API key needed)

```bash
# Install Ollama: https://ollama.com
ollama pull llama3.2      # or: mistral, gemma3, phi3
ollama serve              # start the local server

cp .env.example .env
# In .env set:
#   LLM_PROVIDER=ollama
#   LLM_MODEL=llama3.2

python main.py --check               # verify connection
python main.py --pdf report.pdf      # run analysis
```

#### Option B — Cloud API (Anthropic, OpenAI, Groq, Mistral)

```bash
cp .env.example .env
# In .env set your provider + API key, e.g.:
#   LLM_PROVIDER=anthropic
#   ANTHROPIC_API_KEY=sk-ant-...

python main.py --check               # verify connection
python main.py --pdf report.pdf
```

This produces `report_report.html` — open it in any browser.

### GUI — Streamlit usage

#### Option C — Local Streamlit UI

```bash
streamlit run streamlit_app.py
```

#### Option D — Streamlit Cloud (self-hosted UI)

1) Push this repo to your GitHub.
2) On Streamlit Cloud, create a new app from your repo.
3) Add secrets in **App settings → Secrets** (see `.streamlit/secrets.toml.example`).
4) Deploy. The app stays private to your account/team.

#### Option E — Docker (self-hosted UI)

```bash
docker build -t girafon .
docker run -p 8501:8501 girafon
```

---

## CLI options

```
python main.py --pdf <path>           # Required: path to ESG PDF
               --company "Name"       # Company name for the report header
               --output report.html   # Custom output path
               --json results.json    # Also save raw results as JSON
               --mode original        # "original" (ESRS 2023) or "omnibus" (2026)
               --chunk-words 500      # Chunk size in words (default: 500)
               --overlap-words 120    # Chunk overlap (default: 120)
               --min-chunk-words 40   # Discard very short chunks (default: 40)
               --schema basic         # "basic" (20 disclosures), "ig3-core" (ESRS2+E1+G1), or "ig3" (full datapoints)
               --taxonomy-map map.json # Optional ESRS taxonomy mapping file
```

## ESRS XBRL taxonomy mapping (optional)

If you want JSON outputs to include ESRS taxonomy element IDs, build the mapping once:

```bash
python -m esg_analyzer.taxonomy.build_map \
  --annex /path/to/Annex-1-ESRS-Set1-XBRL-Taxonomy-illustrated-in-Excel.xlsx \
  --out esg_analyzer/frameworks/esrs_taxonomy_map.json
```

When `esrs_taxonomy_map.json` exists, CLI and Streamlit outputs automatically add
`taxonomy_elements` to each disclosure in the JSON report.

## IG3 full datapoint schema (optional)

IG3 contains 1,000+ datapoints and is much slower than the basic 20-disclosure run.
For a faster high-impact scan, use `--schema ig3-core` (ESRS 2 + E1 + G1).

```bash
python -m esg_analyzer.frameworks.build_ig3_schema \
  --ig3 /path/to/EFRAG_IG3_List_of_ESRS_Data_Points.xlsx \
  --out esg_analyzer/frameworks/esrs_ig3_schema.json \
  --base-schema esg_analyzer/frameworks/esrs_schema.json

python main.py --pdf report.pdf --schema ig3-core
python main.py --pdf report.pdf --schema ig3
```

### Materiality-aware scoring (strict)

In IG3 mode, the tool runs a strict materiality scan:
it only treats a topic as non-material if the report explicitly states so.
When a topic is declared non-material, related datapoints are excluded from scoring
and recommendations, while still being listed in the report with a note.
Materiality matrices in markdown tables are also parsed (strictly) when they include
explicit "Not material" columns with marks (e.g. X).

---

## Security & Data Privacy

- **Self-hosted by default.** Run locally or on your own Streamlit Cloud workspace.
- **No data leaves your machine** when using a local LLM (Ollama).
- **API keys are never stored in the UI.** Use `.env` or Streamlit secrets.
- **Advisory only.** Outputs are not legal compliance certificates.

---

## ESRS framework modes

| Mode | Description | Scope |
|------|-------------|-------|
| `original` | Full ESRS Delegated Act (EU) 2023/2772 | Wave 1 companies (FY2024 reports) |
| `omnibus` | Simplified ESRS post-Omnibus Directive (Feb 2026) | 1,000+ FTE / €450M+ turnover |

The Omnibus Directive (adopted 24 Feb 2026) reduced mandatory data points by ~61%. Use `--mode omnibus` to evaluate against the new simplified requirements.

---

## Covered ESRS disclosures

| Section | Name | Category | Mandatory (Original) | Mandatory (Omnibus) |
|---------|------|----------|---------------------|---------------------|
| E1-6 | Scope 1 GHG Emissions | Climate | ✅ | ✅ |
| E1-6 | Scope 2 GHG Emissions | Climate | ✅ | ✅ |
| E1-6 | Scope 3 GHG Emissions | Climate | ✅ | ✅ |
| E1-6 | GHG Emissions Intensity | Climate | ✅ | ⬜ voluntary |
| E1-4 | Climate Targets | Climate | ✅ | ✅ |
| E1-5 | Energy Consumption & Mix | Climate | ✅ | ✅ |
| E1-1 | Transition Plan | Climate | ✅ | ⬜ voluntary |
| E2-4 | Pollution to Air/Water/Soil | Pollution | materiality | materiality |
| E3-4 | Water Consumption | Water | materiality | materiality |
| E4-1 | Biodiversity Policies | Biodiversity | materiality | ⬜ voluntary |
| E5-5 | Waste & Resource Use | Circular Economy | materiality | materiality |
| S1-9 | Gender Diversity | Workforce | ✅ | ✅ |
| S1-14 | Health & Safety (LTIFR) | Workforce | ✅ | ✅ |
| S1-13 | Training Hours | Workforce | ✅ | ⬜ voluntary |
| S1-8 | Collective Bargaining | Workforce | ✅ | ✅ |
| S2-1 | Supply Chain Due Diligence | Value Chain | materiality | materiality |
| G1-1 | Governance Structure | Governance | ✅ | ✅ |
| G1-3 | Anti-Corruption Policy | Governance | ✅ | ✅ |
| G1-4 | Corruption Incidents | Governance | ✅ | ✅ |
| G1-5 | Political Lobbying | Governance | materiality | ⬜ voluntary |

---

## Architecture

```
esg-gap-detector/
├── main.py                              # CLI entry point
├── esg_analyzer/
│   ├── parsers/
│   │   └── pdf_parser.py               # PDF → overlapping text chunks + page numbers
│   ├── frameworks/
│   │   └── esrs_schema.json            # ESRS disclosures (Omnibus-aware)
│   ├── retrieval/
│   │   └── search.py                   # Keyword-based chunk retrieval (TF-IDF style)
│   ├── analysis/
│   │   ├── detector.py                 # LLM-powered disclosure detection
│   │   └── scorer.py                   # Deterministic weighted scoring
│   └── report/
│       └── generator.py               # Self-contained HTML report
```

**Design principles:**
- Framework logic is pure data (JSON), never hardcoded
- LLM is used only to evaluate evidence — scoring is deterministic
- No vector DB required for MVP (keyword search + cosine upgrade path built in)
- Modular: swap any component without touching others

---

## How scoring works

Each ESRS disclosure has a **weight** (1–10). The overall score is:

```
score = Σ(weight_i × status_value_i) / Σ(weight_i) × 100

where status_value: FOUND=1.0, PARTIAL=0.5, MISSING=0.0
```

Weights are configurable in `esrs_schema.json`.

**Score bands:**
| Score | Band |
|-------|------|
| 80–100 | ✅ Excellent |
| 60–79 | 🟡 Good |
| 40–59 | 🟠 Needs Improvement |
| 0–39 | 🔴 Weak / High Risk |

---

## Greenwashing detection

The tool flags vague or unsupported language patterns including:
- *"we aim to be net zero"* without a baseline, timeline, or methodology
- Net-zero claims without interim milestones
- Heavy offset reliance without disclosure of offset type/standard
- Scope 3 total disclosed but categories not enumerated
- Targets without a baseline year

---

## Limitations

- This tool is **advisory only** — it is not a legal compliance certificate
- Accuracy depends on PDF text extraction quality (scanned PDFs may need OCR)
- LLM evaluation can produce false positives/negatives; always human-review the output
- ESRS rules are subject to ongoing regulatory change; schema will be updated

---

## Roadmap

- [ ] Benchmarking vs industry peers
- [ ] Year-over-year trend tracking
- [ ] Semantic search upgrade (sentence-transformers + FAISS)
- [ ] GRI / ISSB cross-mapping
- [ ] Batch processing for multiple companies
- [x] Streamlit web UI (basic)

---

## Contributing

PRs welcome, especially:
- Additional ESRS disclosures in `esrs_schema.json`
- Better keyword lists for existing disclosures
- Sample anonymised test reports

---

## License

MIT — free to use, modify, and distribute.

---

## References

- [EFRAG ESRS Delegated Act (EU) 2023/2772](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202302772)
- [EU Omnibus Directive — Feb 2026](https://eur-lex.europa.eu)
- [GHG Protocol Corporate Standard](https://ghgprotocol.org/corporate-standard)
- [CSRD Overview — EFRAG](https://www.efrag.org/en/projects/esrs-mandatory-standards)
