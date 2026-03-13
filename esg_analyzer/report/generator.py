"""
generator.py
------------
Generates a self-contained HTML report from scoring results.
No external dependencies — pure Python string templating.
The HTML file is fully portable (single file, inline CSS/JS).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_report(
    score_report: Dict[str, Any],
    company_name: str = "Company",
    pdf_filename: str = "",
    output_path: Optional[str] = None,
    mode: str = "original",
) -> str:
    """
    Generate a self-contained HTML audit report.

    Parameters
    ----------
    score_report  : Output of scorer.compute_scores()
    company_name  : Company name to display
    pdf_filename  : Source PDF filename (for reference)
    output_path   : Where to save the HTML file (optional)
    mode          : "original" or "omnibus"

    Returns
    -------
    HTML string
    """
    html = _build_html(score_report, company_name, pdf_filename, mode)

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")
        print(f"  Report saved to: {output_path}")

    return html


# ── HTML builder ───────────────────────────────────────────────────────────────

def _build_html(
    r: Dict[str, Any],
    company_name: str,
    pdf_filename: str,
    mode: str,
) -> str:
    today = date.today().strftime("%d %B %Y")
    score = r["overall_score"]
    band = r["band"]
    cat_scores = r["category_scores"]
    mode_label = "ESRS 2023 (Original)" if mode == "original" else "ESRS Post-Omnibus 2026"

    # Colour for score circle
    score_color = band["color"]

    topic_labels = {
        "ESRS 2": "General Disclosures",
        "E1": "Climate Change",
        "E2": "Pollution",
        "E3": "Water and Marine Resources",
        "E4": "Biodiversity and Ecosystems",
        "E5": "Resource Use and Circular Economy",
        "S1": "Own Workforce",
        "S2": "Workers in the Value Chain",
        "S3": "Affected Communities",
        "S4": "Consumers and End Users",
        "G1": "Business Conduct",
    }

    # Status badge helper
    def badge(status):
        colors = {
            "FOUND":   ("#dcfce7", "#166534", "✓ FOUND"),
            "PARTIAL": ("#fef9c3", "#854d0e", "⚠ PARTIAL"),
            "MISSING": ("#fee2e2", "#991b1b", "✗ MISSING"),
        }
        bg, fg, label = colors.get(status, ("#f3f4f6", "#374151", status))
        return f'<span class="badge" style="background:{bg};color:{fg}">{label}</span>'

    def priority_chip(p):
        if p == "HIGH":
            return '<span class="chip high">HIGH PRIORITY</span>'
        return '<span class="chip medium">MEDIUM</span>'

    # Build disclosure rows
    rows_by_category: Dict[str, list] = {}
    for item in r["per_item"]:
        cat = item.get("category", "Environment")
        if cat not in rows_by_category:
            rows_by_category[cat] = []

        quote_html = ""
        if item.get("best_quote"):
            pg = f" — p.{item['page']}" if item.get("page") else ""
            quote_html = f'<div class="quote">"{item["best_quote"]}"{pg}</div>'

        flags_html = ""
        if item.get("quality_flags"):
            flags_html = "".join(
                f'<div class="flag">⚑ {f}</div>' for f in item["quality_flags"]
            )

        missing_dp_html = ""
        if item.get("data_points_missing"):
            dps = item["data_points_missing"]
            missing_dp_html = f'<div class="missing-dp">Missing: {", ".join(dps[:3])}</div>'

        materiality_html = ""
        if item.get("materiality_status") == "non_material":
            evidence = item.get("materiality_evidence")
            page = item.get("materiality_page")
            pg = f" (p.{page})" if page else ""
            ev = f' — "{evidence}"' if evidence else ""
            materiality_html = f'<div class="materiality-note">Non-material (explicit){pg}{ev}</div>'

        omnibus_note = ""
        if item.get("omnibus_notes"):
            omnibus_note = f'<div class="omnibus-note">🇪🇺 Omnibus: {item["omnibus_notes"]}</div>'

        # GRI cross-reference
        gri_html = ""
        gri = item.get("cross_references", {}).get("GRI", {})
        if gri:
            standards = ", ".join(gri.get("standards", []))
            alignment = gri.get("alignment", "")
            align_color = {"full": "#166534", "partial": "#854d0e", "none": "#991b1b"}.get(alignment, "#374151")
            align_bg    = {"full": "#dcfce7", "partial": "#fef9c3", "none": "#fee2e2"}.get(alignment, "#f3f4f6")
            gri_html = f'<div class="gri-ref"><span class="gri-badge">GRI</span> <span class="gri-standards">{standards}</span> <span class="gri-align" style="background:{align_bg};color:{align_color}">{alignment}</span>'
            if gri.get("gri_stricter") and gri.get("gri_delta"):
                delta_short = gri["gri_delta"][:120] + ("…" if len(gri["gri_delta"]) > 120 else "")
                gri_html += f'<div class="gri-delta">⬆ GRI stricter: {delta_short}</div>'
            gri_html += "</div>"

        mandatory_tag = '<span class="mandatory-tag">MANDATORY</span>' if item.get("is_mandatory") else ""

        rows_by_category[cat].append(f"""
        <tr class="disclosure-row status-{item['status'].lower()}">
          <td class="col-section">
            <span class="section-id">{item['section']}</span>
            {mandatory_tag}
          </td>
          <td class="col-name">
            <strong>{item['name']}</strong>
            <div class="reason">{item.get('reason', '')}</div>
            {quote_html}
            {flags_html}
            {missing_dp_html}
            {materiality_html}
            {gri_html}
            {omnibus_note}
          </td>
          <td class="col-status">{badge(item['status'])}</td>
          <td class="col-weight">{item['weight']}</td>
        </tr>""")

    # Category sections
    category_icons = {"Environment": "🌱", "Social": "👥", "Governance": "⚖️"}
    preferred_order = ["General", "Environment", "Social", "Governance"]
    ordered_categories = [c for c in preferred_order if c in cat_scores]
    ordered_categories += [c for c in cat_scores.keys() if c not in ordered_categories]
    category_sections_html = ""
    for cat in ordered_categories:
        cs = cat_scores[cat]
        rows = rows_by_category.get(cat, [])
        icon = category_icons.get(cat, "")
        category_sections_html += f"""
        <section class="category-section">
          <div class="category-header">
            <h2>{icon} {cat}</h2>
            <div class="category-meta">
              <span class="cat-score" style="color:{_cat_color(cs['score'])}">{cs['score']}/100</span>
              <span class="cat-counts">
                <span class="found-count">{cs['found']} found</span> ·
                <span class="partial-count">{cs['partial']} partial</span> ·
                <span class="missing-count">{cs['missing']} missing</span>
              </span>
            </div>
          </div>
          <div class="progress-bar-wrap">
            <div class="progress-bar" style="width:{cs['score']}%;background:{_cat_color(cs['score'])}"></div>
          </div>
          <table class="disclosure-table">
            <thead>
              <tr>
                <th class="col-section">Section</th>
                <th class="col-name">Disclosure</th>
                <th class="col-status">Status</th>
                <th class="col-weight">Weight</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
        </section>"""

    # Recommendations
    rec_html = ""
    for i, rec in enumerate(r["recommendations"], 1):
        rec_html += f"""
        <div class="rec-item">
          <div class="rec-num">{i}</div>
          <div class="rec-body">
            {priority_chip(rec['priority'])}
            <span class="rec-section">{rec['section']}</span>
            <p class="rec-action">{rec['action']}</p>
          </div>
        </div>"""

    # Quality flags
    qf_html = ""
    if r["quality_flags_summary"]:
        for qf in r["quality_flags_summary"][:8]:
            qf_html += f'<div class="qf-item"><span class="qf-disc">{qf["disclosure"]}</span> — {qf["flag"]}</div>'
    else:
        qf_html = '<p class="no-flags">No greenwashing signals detected in this report.</p>'

    # Materiality summary (explicit non-material only)
    materiality_summary = r.get("materiality_summary", {}) or {}
    non_material_items = []
    for topic, info in materiality_summary.items():
        if info.get("status") != "non_material":
            continue
        label = topic_labels.get(topic, topic)
        page = info.get("page")
        evidence = info.get("evidence") or ""
        evidence_short = (evidence[:140] + "…") if len(evidence) > 140 else evidence
        pg = f" (p.{page})" if page else ""
        ev = f' — "{evidence_short}"' if evidence_short else ""
        non_material_items.append(f"<li><strong>{topic}</strong> — {label}{pg}{ev}</li>")

    if non_material_items:
        materiality_html = (
            '<div class="materiality-summary">'
            '<div class="ms-title">Non-material topics (explicit)</div>'
            f"<ul class=\"ms-list\">{''.join(non_material_items)}</ul>"
            "</div>"
        )
    else:
        materiality_html = (
            '<div class="materiality-summary">'
            '<div class="ms-title">Non-material topics (explicit)</div>'
            '<div class="ms-empty">No explicit non-material topics detected.</div>'
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ESRS Gap Analysis — {company_name}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --navy:    #0d1b2a;
      --ink:     #1e293b;
      --muted:   #64748b;
      --border:  #e2e8f0;
      --bg:      #f8fafc;
      --white:   #ffffff;
      --found:   #22c55e;
      --partial: #f59e0b;
      --missing: #ef4444;
      --accent:  #3b82f6;
    }}

    body {{
      font-family: 'DM Sans', sans-serif;
      font-size: 14px;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.6;
    }}

    /* ── Header ── */
    .report-header {{
      background: var(--navy);
      color: white;
      padding: 48px 64px 40px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 40px;
      align-items: start;
    }}
    .header-left .label {{
      font-size: 11px;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: #94a3b8;
      margin-bottom: 8px;
    }}
    .header-left h1 {{
      font-family: 'DM Serif Display', serif;
      font-size: 32px;
      font-weight: 400;
      line-height: 1.2;
      margin-bottom: 12px;
    }}
    .header-left .meta {{
      font-size: 13px;
      color: #94a3b8;
      display: flex;
      gap: 24px;
      flex-wrap: wrap;
    }}
    .header-left .meta span {{ display: flex; align-items: center; gap: 6px; }}

    /* Score circle */
    .score-circle {{
      width: 120px;
      height: 120px;
      border-radius: 50%;
      border: 4px solid {score_color};
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}
    .score-circle .score-num {{
      font-family: 'DM Serif Display', serif;
      font-size: 36px;
      font-weight: 400;
      color: white;
      line-height: 1;
    }}
    .score-circle .score-label {{
      font-size: 11px;
      color: {score_color};
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-top: 2px;
    }}

    /* ── Stats bar ── */
    .stats-bar {{
      background: white;
      border-bottom: 1px solid var(--border);
      padding: 20px 64px;
      display: flex;
      gap: 40px;
      flex-wrap: wrap;
    }}

    /* ── Materiality summary ── */
    .materiality-summary {{
      background: #f8fafc;
      border-bottom: 1px solid var(--border);
      padding: 16px 64px 14px;
    }}
    .ms-title {{
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .ms-list {{
      list-style: none;
      padding-left: 0;
      margin: 0;
      display: grid;
      gap: 6px;
      color: #475569;
      font-size: 12px;
    }}
    .ms-list li strong {{
      font-family: 'DM Mono', monospace;
      font-weight: 600;
      color: var(--accent);
    }}
    .ms-empty {{
      color: #94a3b8;
      font-size: 12px;
    }}
    .stat {{
      display: flex;
      flex-direction: column;
    }}
    .stat-val {{
      font-family: 'DM Serif Display', serif;
      font-size: 24px;
      color: var(--ink);
    }}
    .stat-lbl {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    .stat-val.green {{ color: var(--found); }}
    .stat-val.amber {{ color: var(--partial); }}
    .stat-val.red   {{ color: var(--missing); }}

    /* ── Main content ── */
    .main-content {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 40px 64px 80px;
    }}

    /* ── Recommendations ── */
    .section-title {{
      font-family: 'DM Serif Display', serif;
      font-size: 22px;
      font-weight: 400;
      color: var(--ink);
      margin-bottom: 20px;
      padding-bottom: 10px;
      border-bottom: 2px solid var(--border);
    }}

    .recs-section {{ margin-bottom: 48px; }}
    .rec-item {{
      display: flex;
      gap: 16px;
      padding: 16px 0;
      border-bottom: 1px solid var(--border);
    }}
    .rec-num {{
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: var(--navy);
      color: white;
      font-size: 12px;
      font-weight: 600;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      margin-top: 2px;
    }}
    .rec-body {{ flex: 1; }}
    .rec-action {{ font-size: 14px; color: var(--ink); margin-top: 4px; }}
    .rec-section {{
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      background: #f1f5f9;
      padding: 2px 6px;
      border-radius: 3px;
      color: var(--muted);
      margin-left: 6px;
    }}

    .chip {{
      display: inline-block;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 2px 8px;
      border-radius: 3px;
    }}
    .chip.high   {{ background: #fee2e2; color: #991b1b; }}
    .chip.medium {{ background: #fef9c3; color: #854d0e; }}

    /* ── Category sections ── */
    .category-section {{
      margin-bottom: 48px;
    }}
    .category-header {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 12px;
    }}
    .category-header h2 {{
      font-family: 'DM Serif Display', serif;
      font-size: 20px;
      font-weight: 400;
    }}
    .category-meta {{
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    .cat-score {{
      font-family: 'DM Serif Display', serif;
      font-size: 20px;
    }}
    .cat-counts {{
      font-size: 12px;
      color: var(--muted);
    }}
    .found-count   {{ color: var(--found); font-weight: 500; }}
    .partial-count {{ color: var(--partial); font-weight: 500; }}
    .missing-count {{ color: var(--missing); font-weight: 500; }}

    .progress-bar-wrap {{
      height: 4px;
      background: var(--border);
      border-radius: 2px;
      margin-bottom: 20px;
      overflow: hidden;
    }}
    .progress-bar {{
      height: 100%;
      border-radius: 2px;
      transition: width 0.6s ease;
    }}

    /* ── Disclosure table ── */
    .disclosure-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .disclosure-table thead tr {{
      border-bottom: 2px solid var(--border);
    }}
    .disclosure-table th {{
      padding: 8px 12px;
      text-align: left;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .disclosure-table td {{
      padding: 12px;
      vertical-align: top;
      border-bottom: 1px solid var(--border);
    }}
    .disclosure-row:hover {{ background: #f8fafc; }}

    .col-section {{ width: 120px; }}
    .col-status  {{ width: 110px; }}
    .col-weight  {{ width: 60px; text-align: center; color: var(--muted); }}

    .section-id {{
      font-family: 'DM Mono', monospace;
      font-size: 12px;
      font-weight: 500;
      color: var(--accent);
      display: block;
      margin-bottom: 4px;
    }}
    .mandatory-tag {{
      font-size: 9px;
      background: #eff6ff;
      color: #1d4ed8;
      padding: 1px 5px;
      border-radius: 2px;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}

    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
    }}

    .reason {{
      font-size: 12px;
      color: var(--muted);
      margin-top: 4px;
      line-height: 1.5;
    }}
    .quote {{
      margin-top: 8px;
      padding: 8px 12px;
      background: #f8fafc;
      border-left: 3px solid var(--accent);
      font-size: 12px;
      color: #334155;
      font-style: italic;
      border-radius: 0 4px 4px 0;
    }}
    .flag {{
      font-size: 11px;
      color: #b45309;
      margin-top: 4px;
    }}
    .missing-dp {{
      font-size: 11px;
      color: var(--missing);
      margin-top: 4px;
    }}
    .omnibus-note {{
      font-size: 11px;
      color: #7c3aed;
      margin-top: 4px;
      padding: 4px 8px;
      background: #f5f3ff;
      border-radius: 4px;
    }}

    /* ── GRI cross-references ── */
    .gri-ref {{
      margin-top: 6px;
      font-size: 11px;
      color: var(--muted);
    }}
    .gri-badge {{
      display: inline-block;
      background: #1e3a5f;
      color: white;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.1em;
      padding: 1px 5px;
      border-radius: 2px;
      margin-right: 4px;
    }}
    .gri-standards {{
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      color: #1e3a5f;
      margin-right: 6px;
    }}
    .gri-align {{
      display: inline-block;
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 1px 5px;
      border-radius: 2px;
    }}
    .gri-delta {{
      margin-top: 3px;
      padding: 4px 8px;
      background: #fffbeb;
      border-left: 3px solid #f59e0b;
      font-size: 11px;
      color: #78350f;
      border-radius: 0 3px 3px 0;
    }}
    .materiality-note {{
      margin-top: 6px;
      padding: 6px 8px;
      background: #f1f5f9;
      border-left: 3px solid #94a3b8;
      color: #475569;
      font-size: 12px;
      border-radius: 0 3px 3px 0;
    }}

    /* ── Greenwashing flags ── */
    .flags-section {{ margin-bottom: 48px; }}
    .qf-item {{
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }}
    .qf-disc {{
      font-weight: 600;
      color: var(--ink);
    }}
    .no-flags {{
      color: var(--found);
      font-size: 14px;
      padding: 12px 0;
    }}

    /* ── Footer ── */
    .report-footer {{
      background: var(--navy);
      color: #64748b;
      padding: 24px 64px;
      font-size: 11px;
      line-height: 1.7;
    }}

    /* ── Print ── */
    @media print {{
      body {{ background: white; }}
      .stats-bar, .report-header {{ break-inside: avoid; }}
      .category-section {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>

<!-- ── Header ── -->
<header class="report-header">
  <div class="header-left">
    <div class="label">ESRS Gap Analysis Report</div>
    <h1>{company_name}</h1>
    <div class="meta">
      <span>📅 {today}</span>
      <span>📋 Framework: {mode_label}</span>
      {'<span>📄 Source: ' + pdf_filename + '</span>' if pdf_filename else ''}
      <span>🔍 {r['total_disclosures']} disclosures analysed</span>
    </div>
  </div>
  <div class="score-circle">
    <span class="score-num">{score}</span>
    <span class="score-label">{band['label']}</span>
  </div>
</header>

{materiality_html}

<!-- ── Stats bar ── -->
<div class="stats-bar">
  <div class="stat">
    <span class="stat-val green">{r['found_count']}</span>
    <span class="stat-lbl">Disclosures Found</span>
  </div>
  <div class="stat">
    <span class="stat-val amber">{r['partial_count']}</span>
    <span class="stat-lbl">Partial</span>
  </div>
  <div class="stat">
    <span class="stat-val red">{r['missing_count']}</span>
    <span class="stat-lbl">Missing</span>
  </div>
  <div class="stat">
    <span class="stat-val" style="color:var(--accent)">{r['compliance_rate']}%</span>
    <span class="stat-lbl">Mandatory Compliance</span>
  </div>
  <div class="stat">
    <span class="stat-val red">{r['mandatory_missing']}</span>
    <span class="stat-lbl">Mandatory Missing</span>
  </div>
  <div class="stat">
    <span class="stat-val" style="color:#1e3a5f">{sum(1 for i in r['per_item'] if i.get('cross_references', {}).get('GRI', {}).get('gri_stricter'))}</span>
    <span class="stat-lbl">GRI Stricter Gaps</span>
  </div>
</div>

<main class="main-content">

  <!-- ── Recommendations ── -->
  <section class="recs-section">
    <h2 class="section-title">Top Improvement Actions</h2>
    {rec_html if rec_html else '<p style="color:var(--muted)">No critical improvements identified.</p>'}
  </section>

  <!-- ── Greenwashing flags ── -->
  <section class="flags-section">
    <h2 class="section-title">⚑ Greenwashing Risk Signals</h2>
    {qf_html}
  </section>

  <!-- ── Per-category disclosure tables ── -->
  <section>
    <h2 class="section-title">Disclosure Detail</h2>
    {category_sections_html}
  </section>

</main>

<footer class="report-footer">
  <strong style="color:#94a3b8">ESRS Gap Detector</strong> — Open source tool · github.com/your-handle/esg-gap-detector<br>
  ⚠️ This report is advisory only and does not constitute a legal compliance certificate. ESRS rules are subject to change following the EU Omnibus Directive (Feb 2026). Always validate with a qualified auditor.<br>
  Framework reference: EFRAG ESRS Delegated Act (EU) 2023/2772 · Omnibus Directive (EU) 2026 · GHG Protocol Corporate Standard
</footer>

</body>
</html>"""


def _cat_color(score: float) -> str:
    if score >= 80:
        return "#22c55e"
    elif score >= 60:
        return "#84cc16"
    elif score >= 40:
        return "#f59e0b"
    return "#ef4444"
