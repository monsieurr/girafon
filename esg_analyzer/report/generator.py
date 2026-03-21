"""
generator.py
------------
Generates a single-file HTML report from scoring results.
Pure Python string templating with inline CSS/JS.
Works locally in a browser; uses web fonts when available with system fallbacks.
"""

from __future__ import annotations

import html
import json
import re
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
    schema_profile: Optional[str] = None,
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
    report_html = _build_html(score_report, company_name, pdf_filename, mode, schema_profile)

    if output_path:
        Path(output_path).write_text(report_html, encoding="utf-8")
        print(f"  Report saved to: {output_path}")

    return report_html


# ── HTML builder ───────────────────────────────────────────────────────────────

def _build_html(
    r: Dict[str, Any],
    company_name: str,
    pdf_filename: str,
    mode: str,
    schema_profile: Optional[str],
) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def esc_attr(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    today = date.today().strftime("%d %B %Y")
    score = r["overall_score"]
    band = r["band"]
    cat_scores = r["category_scores"]
    if mode == "original":
        mode_label = "ESRS Set 1 (Delegated Act 2023)"
        mode_badge = ""
        mode_note = ""
        mode_header_note = ""
        framework_ref = "ESRS Set 1 Delegated Act (EU) 2023/2772 · GHG Protocol Corporate Standard"
    else:
        mode_label = "ESRS Simplified / Omnibus (draft)"
        mode_badge = '<span class="mode-pill">Draft</span>'
        mode_note = (
            "Simplified/Omnibus settings are based on EFRAG technical advice (Dec 2025) "
            "and are not yet adopted law. ESRS Set 1 (EU 2023/2772) remains applicable."
        )
        mode_header_note = "⚠️ Draft (not adopted law). ESRS Set 1 remains applicable."
        framework_ref = (
            "ESRS Set 1 Delegated Act (EU) 2023/2772 · Simplified ESRS (Omnibus draft) · "
            "GHG Protocol Corporate Standard"
        )

    profile_label = ""
    profile_note = ""
    if schema_profile:
        profile_key = schema_profile.strip().lower()
        if profile_key == "basic":
            profile_label = "Basic (Girafon 20-key disclosures)"
            profile_note = "Basic is Girafon’s fast-scan subset for quick triage."
        elif profile_key == "ig3-core":
            profile_label = "IG3-core (ESRS 2 + E1 + G1)"
            profile_note = "IG3-core is a Girafon preset subset of IG3."
        elif profile_key == "ig3":
            profile_label = "IG3 full (EFRAG guidance)"
            profile_note = "IG3 is EFRAG implementation guidance (non-authoritative)."

    # Colour for score circle (explicit thresholds)
    try:
        score_val = float(score)
    except (TypeError, ValueError):
        score_val = 0.0
    if score_val < 50:
        score_color = "#ef4444"
    elif score_val < 75:
        score_color = "#f59e0b"
    else:
        score_color = "#22c55e"

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

    high_priority_sections = {
        rec.get("section")
        for rec in r.get("recommendations", [])
        if rec.get("priority") == "HIGH"
    }

    llm_info = r.get("llm_info") or {}
    llm_meta_html = ""
    if llm_info:
        provider = llm_info.get("provider") or "unknown"
        model = llm_info.get("model") or "unknown"
        state = llm_info.get("state") or "unknown"
        fallback_count = llm_info.get("fallback_count")
        total = llm_info.get("total_disclosures")
        state_labels = {
            "connected": "connected",
            "unreachable": "not connected - keyword fallback",
            "configured": "configured (connectivity not verified)",
            "missing_key": "missing API key",
            "unknown": "unknown",
        }
        state_label = state_labels.get(state, state)
        fallback_label = ""
        if isinstance(fallback_count, int) and isinstance(total, int):
            fallback_label = f" · fallback {fallback_count}/{total}"
        llm_meta_html = (
            f"<span>🤖 LLM: {esc(provider)} / {esc(model)} - {esc(state_label)}"
            f"{esc(fallback_label)}</span>"
        )

    def confidence_level(item: Dict[str, Any]) -> tuple[str, str]:
        if item.get("status") == "FOUND" and item.get("best_quote") and item.get("page"):
            return ("High", "high")
        if item.get("status") == "PARTIAL":
            return ("Medium", "medium")
        if item.get("status") == "FOUND":
            return ("Medium", "medium")
        return ("Low", "low")

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

    def slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")

    # Build disclosure rows
    rows_by_category: Dict[str, list] = {}
    for item in r["per_item"]:
        cat = item.get("category", "Environment")
        if cat not in rows_by_category:
            rows_by_category[cat] = []

        conf_label, conf_class = confidence_level(item)
        confidence_html = f'<span class="conf-badge {conf_class}">{conf_label} confidence</span>'

        quote_html = ""
        if item.get("best_quote"):
            pg = f"p.{item['page']}" if item.get("page") else ""
            quote_text = esc(item.get("best_quote"))
            evidence_meta_html = (
                f'<div class="evidence-meta">Source: {esc(pg)}</div>' if pg else ""
            )
            quote_html = (
                '<div class="evidence-block">'
                '<div class="evidence-label">Evidence</div>'
                f'<div class="quote">"{quote_text}"</div>'
                f"{evidence_meta_html}"
                "</div>"
            )

        flags_html = ""
        if item.get("quality_flags"):
            chips = "".join(
                f'<span class="flag-chip">{esc(f)}</span>' for f in item["quality_flags"]
            )
            flags_html = f'<div class="flag-row">{chips}</div>'

        missing_dp_html = ""
        if item.get("data_points_missing"):
            dps = item["data_points_missing"]
            dps_text = ", ".join(esc(dp) for dp in dps)
            missing_dp_html = (
                '<div class="detail-block">'
                '<div class="detail-label">Missing data points</div>'
                f'<div class="detail-body">{dps_text}</div>'
                "</div>"
            )

        rationale_html = ""
        if item.get("reason"):
            rationale_html = (
                '<div class="evidence-block">'
                '<div class="evidence-label">Rationale</div>'
                f'<div class="evidence-body">{esc(item.get("reason", ""))}</div>'
                "</div>"
            )

        materiality_html = ""
        if item.get("materiality_status") == "non_material":
            evidence = item.get("materiality_evidence")
            page = item.get("materiality_page")
            pg = f" (p.{page})" if page else ""
            ev = f' - "{esc(evidence)}"' if evidence else ""
            materiality_html = f'<div class="materiality-note">Non-material (explicit){esc(pg)}{ev}</div>'

        omnibus_note = ""
        if item.get("omnibus_notes"):
            omnibus_note = f'<div class="detail-block"><div class="detail-label">Omnibus note</div><div class="detail-body">{esc(item["omnibus_notes"])}</div></div>'

        # GRI cross-reference
        gri_html = ""
        gri = item.get("cross_references", {}).get("GRI", {})
        if gri:
            standards = ", ".join(esc(s) for s in gri.get("standards", []))
            alignment_raw = str(gri.get("alignment", "") or "")
            alignment = alignment_raw.lower()
            align_color = {"full": "#166534", "partial": "#854d0e", "none": "#991b1b"}.get(alignment, "#374151")
            align_bg    = {"full": "#dcfce7", "partial": "#fef9c3", "none": "#fee2e2"}.get(alignment, "#f3f4f6")
            gri_html = f'<div class="detail-block"><div class="detail-label">GRI cross-reference</div><div class="gri-ref"><span class="gri-badge">GRI</span> <span class="gri-standards">{standards}</span> <span class="gri-align" style="background:{align_bg};color:{align_color}">{esc(alignment_raw)}</span>'
            if gri.get("gri_stricter") and gri.get("gri_delta"):
                delta_raw = str(gri["gri_delta"])
                gri_html += f'<div class="gri-delta">GRI stricter: {esc(delta_raw)}</div>'
            gri_html += "</div></div>"

        mandatory_title = (
            "Mandatory if the topic is material. Only ESRS 2 Appendix B datapoints "
            "linked to SFDR, Pillar 3 or EU Taxonomy are mandatory regardless of materiality."
        )
        mandatory_tag = (
            f'<span class="mandatory-tag" title="{esc_attr(mandatory_title)}">MANDATORY</span>'
            if item.get("is_mandatory") else ""
        )
        e1_note_html = ""
        if str(item.get("section", "")).startswith("E1-"):
            e1_note_html = (
                '<div class="e1-note">E1 note: Climate Change may be excluded only with a detailed explanation '
                "(ESRS 1 §32).</div>"
            )

        review_html = f"""
        <div class="review-block">
          <label class="review-label" for="review-{esc_attr(item['key'])}">Review</label>
          <select class="review-select" id="review-{esc_attr(item['key'])}" data-review-key="{esc_attr(item['key'])}" aria-label="Review status">
            <option value="to-review">To review</option>
            <option value="validated">Validated</option>
            <option value="dismissed">Dismissed</option>
          </select>
        </div>
        """

        search_blob = " ".join(
            [
                str(item.get("section", "")),
                str(item.get("name", "")),
                " ".join(item.get("quality_flags", []) or []),
                " ".join(item.get("data_points_missing", []) or []),
                str(item.get("best_quote", "")),
            ]
        ).lower()
        search_blob = esc_attr(search_blob)
        data_status = str(item.get("status", "")).lower()
        data_mandatory = "true" if item.get("is_mandatory") else "false"
        data_priority = "high" if item.get("section") in high_priority_sections else "normal"
        data_page = esc_attr(item.get("page") or "")
        data_quote = esc_attr(item.get("best_quote") or "")

        details_parts = []
        if missing_dp_html:
            details_parts.append(missing_dp_html)
        if gri_html:
            details_parts.append(gri_html)
        if omnibus_note:
            details_parts.append(omnibus_note)
        details_html = ""
        if details_parts:
            details_html = (
                '<details class="row-details">'
                '<summary>Details</summary>'
                f"{''.join(details_parts)}"
                "</details>"
            )

        rows_by_category[cat].append(f"""
        <tr class="disclosure-row status-{item['status'].lower()}"
            data-key="{esc_attr(item['key'])}"
            data-section="{esc_attr(item['section'])}"
            data-name="{esc_attr(item['name'])}"
            data-status="{esc_attr(data_status)}"
            data-mandatory="{esc_attr(data_mandatory)}"
            data-priority="{esc_attr(data_priority)}"
            data-page="{data_page}"
            data-quote="{data_quote}"
            data-search="{search_blob}">
          <td class="col-section">
            <span class="section-id">{esc(item['section'])}</span>
            {mandatory_tag}
          </td>
          <td class="col-name">
            <strong>{esc(item['name'])}</strong>
            {e1_note_html}
            {rationale_html}
            {quote_html}
            {flags_html}
            {materiality_html}
            {details_html}
            {review_html}
          </td>
          <td class="col-status">
            {badge(item['status'])}
            {confidence_html}
          </td>
          <td class="col-weight">{item['weight']}</td>
        </tr>""")

    # Category sections
    category_classes = {"Environment": "env", "Social": "soc", "Governance": "gov", "General": "gen"}
    preferred_order = ["General", "Environment", "Social", "Governance"]
    ordered_categories = [c for c in preferred_order if c in cat_scores]
    ordered_categories += [c for c in cat_scores.keys() if c not in ordered_categories]
    category_nav_html = ""
    if ordered_categories:
        links = []
        for cat in ordered_categories:
            safe_cat = esc(cat)
            cat_id = f"cat-{slugify(cat)}"
            links.append(f'<a class="cat-link" href="#{cat_id}">{safe_cat}</a>')
        category_nav_html = f'<div class="category-jump">Jump to: {"".join(links)}</div>'
        sidebar_links = []
        for cat in ordered_categories:
            safe_cat = esc(cat)
            cat_id = f"cat-{slugify(cat)}"
            sidebar_links.append(f'<a href="#{cat_id}">{safe_cat}</a>')
        sidebar_html = f"""
        <aside class="category-sidebar" aria-label="Category navigation">
          <div class="sidebar-title">Sections</div>
          {''.join(sidebar_links)}
        </aside>
        """
    else:
        sidebar_html = ""
    category_sections_html = ""
    for cat in ordered_categories:
        cs = cat_scores[cat]
        rows = rows_by_category.get(cat, [])
        safe_cat = esc(cat)
        cat_id = f"cat-{slugify(cat)}"
        cat_class = category_classes.get(cat, "gen")
        category_sections_html += f"""
        <section class="category-section" id="{cat_id}">
          <div class="category-header">
            <h2><span class="category-dot {cat_class}"></span>{safe_cat}</h2>
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
          <div class="weight-legend">Weight = contribution to overall score (higher = more impact).</div>
          <div class="table-scroll">
            <table class="disclosure-table">
              <thead>
                <tr>
                  <th class="col-section">Section</th>
                  <th class="col-name">Disclosure</th>
                  <th class="col-status">Status</th>
                  <th class="col-weight"><abbr title="Importance weight in scoring (higher = more impact)">Weight</abbr></th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>
        </section>"""

    # Recommendations
    rec_html = ""
    for i, rec in enumerate(r["recommendations"], 1):
        rec_section = esc(rec.get("section", ""))
        rec_action = esc(rec.get("action", ""))
        rec_html += f"""
        <div class="rec-item">
          <div class="rec-num">{i}</div>
          <div class="rec-body">
            {priority_chip(rec['priority'])}
            <span class="rec-section">{rec_section}</span>
            <p class="rec-action">{rec_action}</p>
          </div>
        </div>"""

    # Quality flags
    qf_html = ""
    if r["quality_flags_summary"]:
        for qf in r["quality_flags_summary"][:8]:
            qf_html += f'<div class="qf-item"><span class="qf-disc">{esc(qf["disclosure"])}</span> - {esc(qf["flag"])}</div>'
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
        ev = f' - "{esc(evidence_short)}"' if evidence_short else ""
        non_material_items.append(f"<li><strong>{esc(topic)}</strong> - {esc(label)}{esc(pg)}{ev}</li>")

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

    # Executive summary blocks
    top_recs = r.get("recommendations", [])[:3]
    if top_recs:
        fix_items = []
        for rec in top_recs:
            priority_raw = str(rec.get("priority", "") or "")
            priority_class = "high" if priority_raw == "HIGH" else "medium"
            fix_items.append(
                f'<li><span class="fix-priority {priority_class}">{esc(priority_raw)}</span> '
                f'<span class="fix-section">{esc(rec.get("section", ""))}</span> {esc(rec.get("action", ""))}</li>'
            )
        fix_list = "".join(fix_items)
    else:
        fix_list = "<li>No critical improvements identified.</li>"

    audit_risks = [
        f"Mandatory missing: {r['mandatory_missing']}",
        f"Compliance rate: {r['compliance_rate']}%",
        f"High priority gaps: {len(high_priority_sections)}",
        f"Greenwashing flags: {len(r['quality_flags_summary'])}",
    ]
    audit_list = "".join(f"<li>{html.escape(str(item))}</li>" for item in audit_risks)

    exec_summary_html = f"""
    <section class="exec-summary">
      <div class="summary-card">
        <h3>What to fix first</h3>
        <ul class="fix-list">{fix_list}</ul>
      </div>
      <div class="summary-card">
        <h3>Audit risk summary</h3>
        <ul class="risk-list">{audit_list}</ul>
      </div>
    </section>
    """

    disclosure_notes_html = """
    <div class="tool-note">
      <strong>Mandatory note:</strong> “MANDATORY” means mandatory <em>if the topic is material</em>. Only ESRS 2 Appendix B datapoints (SFDR/Pillar 3/EU Taxonomy-linked) are mandatory regardless of materiality.
    </div>
    <div class="tool-note">
      <strong>E1-6 structure note:</strong> ESRS E1-6 is a single disclosure requirement. Girafon displays its datapoints (Scopes 1–3 and GHG intensity) as separate rows for gap-analysis granularity.
    </div>
    """

    disclosure_tools_html = f"""
    <div class="disclosure-tools" id="disclosure-tools">
      <div class="tool-group filter-buttons">
        <span class="tool-label">Status</span>
        <button type="button" class="filter-btn active" data-filter-status="all">All</button>
        <button type="button" class="filter-btn" data-filter-status="found">Found</button>
        <button type="button" class="filter-btn" data-filter-status="partial">Partial</button>
        <button type="button" class="filter-btn" data-filter-status="missing">Missing</button>
      </div>
      <div class="tool-group">
        <label>Details</label>
        <div class="details-toggle">
          <button id="expand-details" type="button">Expand all</button>
          <button id="collapse-details" type="button">Collapse all</button>
        </div>
      </div>
      <div class="tool-group">
        <label>Sections</label>
        <div class="details-toggle">
          <button id="expand-sections" type="button">Expand sections</button>
          <button id="collapse-sections" type="button">Collapse sections</button>
        </div>
      </div>
      <div class="tool-group">
        <label><input type="checkbox" id="filter-mandatory"> Mandatory only</label>
      </div>
      <div class="tool-group">
        <label><input type="checkbox" id="filter-priority"> High priority only</label>
      </div>
      <div class="tool-group search">
        <label for="filter-search">Search</label>
        <input type="text" id="filter-search" placeholder="ID, title, missing datapoints, quotes" aria-label="Search disclosures">
      </div>
      <div class="tool-group">
        <label for="filter-reset">Reset</label>
        <button id="filter-reset" type="button" aria-label="Reset filters">Reset filters</button>
      </div>
      <div class="tool-group results">
        <label>Results</label>
        <div id="filter-count" aria-live="polite">0 of 0</div>
      </div>
      <div class="tool-group export">
        <button id="export-json" aria-label="Export reviewed findings as JSON">Export reviewed (JSON)</button>
        <button id="export-csv" aria-label="Export reviewed findings as CSV">Export reviewed (CSV)</button>
      </div>
    </div>
    {disclosure_notes_html}
    {category_nav_html}
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ESRS Gap Analysis - {esc(company_name)}</title>
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

    /* ── Top navigation ── */
    .top-nav {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(13, 27, 42, 0.98);
      color: white;
      padding: 10px 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .nav-brand {{
      font-family: 'DM Serif Display', serif;
      font-size: 16px;
      letter-spacing: 0.02em;
    }}
    .nav-links {{
      display: flex;
      gap: 16px;
      font-size: 12px;
      flex-wrap: wrap;
    }}
    .nav-links .nav-back {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.16);
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      letter-spacing: 0.06em;
    }}
    .nav-links a {{
      color: #dbeafe;
      text-decoration: none;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .nav-links a:hover {{
      color: white;
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
    .meta-disclaimer {{
      color: #b45309;
      font-weight: 600;
    }}

    /* Score circle */
    .score-circle {{
      width: 132px;
      height: 132px;
      border-radius: 50%;
      border: 4px solid {score_color};
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 10px 0;
      flex-shrink: 0;
    }}
    .score-circle .score-num {{
      font-family: 'DM Serif Display', serif;
      font-size: 34px;
      font-weight: 400;
      color: white;
      line-height: 1;
    }}
    .score-circle .score-label {{
      font-size: 8px;
      color: {score_color};
      letter-spacing: 0.08em;
      text-transform: uppercase;
      line-height: 1.3;
      text-align: center;
      max-width: 88px;
    }}

    /* ── Executive summary ── */
    .exec-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
      padding: 24px 64px 0;
    }}
    .summary-card {{
      background: white;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
      box-shadow: 0 6px 14px rgba(15, 23, 42, 0.05);
    }}
    .summary-card h3 {{
      font-family: 'DM Serif Display', serif;
      font-size: 18px;
      font-weight: 400;
      margin-bottom: 8px;
    }}
    .fix-list, .risk-list {{
      list-style: none;
      display: grid;
      gap: 6px;
      font-size: 13px;
    }}
    .fix-priority {{
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 2px 6px;
      border-radius: 3px;
      margin-right: 6px;
    }}
    .fix-priority.high {{ background: #fee2e2; color: #991b1b; }}
    .fix-priority.medium {{ background: #fef9c3; color: #854d0e; }}
    .fix-section {{
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      color: var(--muted);
      margin-right: 6px;
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
    .category-sidebar {{
      position: sticky;
      top: 88px;
      float: right;
      width: 170px;
      margin: 0 0 20px 24px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #f8fafc;
      font-size: 12px;
    }}
    .category-sidebar .sidebar-title {{
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .category-sidebar a {{
      display: block;
      text-decoration: none;
      color: var(--ink);
      padding: 4px 0;
      font-weight: 600;
    }}
    @media (max-width: 1200px) {{
      .category-sidebar {{
        position: static;
        float: none;
        width: auto;
        margin: 0 0 16px 0;
      }}
    }}

    /* ── Disclosure tools ── */
    .disclosure-tools {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: end;
      padding: 12px 0 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 12px;
    }}
    .tool-note {{
      font-size: 12px;
      color: var(--muted);
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      margin: 0 0 10px;
      line-height: 1.5;
    }}
    .tool-group {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
    }}
    .tool-group.filter-buttons {{
      flex-direction: row;
      align-items: center;
      gap: 8px;
    }}
    .tool-label {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 700;
      margin-right: 4px;
    }}
    .tool-group.search {{
      flex: 1;
      min-width: 220px;
    }}
    .tool-group label {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .tool-group input[type="text"],
    .tool-group select {{
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 13px;
      color: var(--ink);
    }}
    .tool-group button {{
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 12px;
      font-weight: 600;
      background: #f8fafc;
      color: var(--ink);
      cursor: pointer;
    }}
    .details-toggle {{
      display: flex;
      gap: 6px;
    }}
    .details-toggle button {{
      padding: 6px 8px;
      font-size: 11px;
    }}
    .filter-btn {{
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--ink);
      font-size: 11px;
      font-weight: 700;
    }}
    .filter-btn.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }}
    .tool-group.export {{
      flex-direction: row;
      gap: 10px;
      align-items: center;
    }}
    .tool-group.export button {{
      background: var(--navy);
      color: white;
      border: none;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .tool-group.export button:hover {{
      background: #111827;
    }}
    .tool-group input[type="text"]:focus,
    .tool-group select:focus,
    .tool-group button:focus,
    .review-select:focus {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .tool-group.results {{
      min-width: 130px;
    }}
    #filter-count {{
      font-size: 12px;
      color: var(--ink);
      font-weight: 600;
    }}

    .category-jump {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 6px 0 18px;
      font-size: 12px;
      color: var(--muted);
    }}
    .category-jump .cat-link {{
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 999px;
      text-decoration: none;
      color: var(--ink);
      background: #f8fafc;
      font-weight: 600;
      font-size: 11px;
    }}

    .category-dot {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 8px;
      vertical-align: middle;
    }}
    .category-dot.env {{ background: #22c55e; }}
    .category-dot.soc {{ background: #3b82f6; }}
    .category-dot.gov {{ background: #f59e0b; }}
    .category-dot.gen {{ background: #64748b; }}

    .weight-legend {{
      font-size: 11px;
      color: var(--muted);
      margin: 8px 0 4px;
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
    .category-section.collapsed .disclosure-table,
    .category-section.collapsed .weight-legend {{
      display: none;
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
    .table-scroll {{
      width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
      -webkit-overflow-scrolling: touch;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: white;
    }}
    .disclosure-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      min-width: 760px;
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
    .disclosure-table th abbr {{
      text-decoration: underline dotted;
      cursor: help;
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
    .mode-pill {{
      display: inline-block;
      margin-left: 6px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: #fef3c7;
      color: #92400e;
      border: 1px solid #f59e0b;
    }}
    .conf-badge {{
      display: inline-block;
      margin-top: 6px;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .conf-badge.high {{ background: #dcfce7; color: #166534; }}
    .conf-badge.medium {{ background: #fef9c3; color: #854d0e; }}
    .conf-badge.low {{ background: #fee2e2; color: #991b1b; }}

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
    .e1-note {{
      margin-top: 6px;
      font-size: 11px;
      color: var(--muted);
      background: #f8fafc;
      border: 1px dashed var(--border);
      border-radius: 6px;
      padding: 6px 8px;
      line-height: 1.4;
    }}
    .flag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }}
    .flag-chip {{
      font-size: 10px;
      color: #b45309;
      background: #fff7ed;
      border: 1px solid #fed7aa;
      padding: 2px 6px;
      border-radius: 999px;
    }}
    .evidence-block {{
      margin-top: 8px;
      padding: 8px 12px;
      background: #f8fafc;
      border-left: 3px solid #cbd5f5;
      border-radius: 0 4px 4px 0;
    }}
    .evidence-label {{
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .evidence-body {{
      font-size: 12px;
      color: var(--ink);
      line-height: 1.5;
    }}
    .evidence-meta {{
      font-size: 11px;
      color: var(--muted);
      margin-top: 6px;
    }}
    .row-details {{
      margin-top: 8px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fbfdff;
      padding: 6px 10px;
    }}
    .row-details summary {{
      cursor: pointer;
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-weight: 700;
      list-style: none;
    }}
    .row-details summary::-webkit-details-marker {{
      display: none;
    }}
    .detail-block {{
      margin-top: 8px;
      padding-top: 6px;
      border-top: 1px dashed var(--border);
    }}
    .detail-block:first-of-type {{
      border-top: none;
      padding-top: 0;
    }}
    .detail-label {{
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .detail-body {{
      font-size: 12px;
      color: var(--ink);
      line-height: 1.5;
    }}
    mark.search-hit {{
      background: #fde68a;
      color: #92400e;
      padding: 0 2px;
      border-radius: 2px;
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
      white-space: normal;
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
    .review-block {{
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 8px;
      padding-top: 6px;
      border-top: 1px dashed var(--border);
    }}
    .review-label {{
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .review-select {{
      font-size: 12px;
      padding: 4px 6px;
      border-radius: 6px;
      border: 1px solid var(--border);
      color: var(--ink);
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

    /* ── Responsive ── */
    @media (max-width: 1024px) {{
      .top-nav,
      .report-header,
      .exec-summary,
      .stats-bar,
      .materiality-summary,
      .main-content,
      .report-footer {{
        padding-left: 28px;
        padding-right: 28px;
      }}
      .report-header {{
        grid-template-columns: 1fr;
        gap: 24px;
      }}
      .score-circle {{
        justify-self: start;
      }}
      .disclosure-tools {{
        gap: 12px;
      }}
    }}

    @media (max-width: 768px) {{
      .top-nav {{
        position: static;
        padding-top: 12px;
        padding-bottom: 12px;
        align-items: flex-start;
        flex-direction: column;
        gap: 10px;
      }}
      .nav-links {{
        width: 100%;
        gap: 10px;
        overflow-x: auto;
        white-space: nowrap;
        flex-wrap: nowrap;
        padding-bottom: 4px;
      }}
      .report-header,
      .exec-summary,
      .stats-bar,
      .materiality-summary,
      .main-content,
      .report-footer {{
        padding-left: 16px;
        padding-right: 16px;
      }}
      .report-header {{
        padding-top: 28px;
        padding-bottom: 24px;
      }}
      .header-left h1 {{
        font-size: 26px;
      }}
      .header-left .meta {{
        flex-direction: column;
        align-items: flex-start;
        gap: 8px;
      }}
      .score-circle {{
        width: 118px;
        height: 118px;
      }}
      .score-circle .score-num {{
        font-size: 30px;
      }}
      .stats-bar {{
        gap: 18px 20px;
      }}
      .stat {{
        min-width: 130px;
      }}
      .category-header {{
        flex-direction: column;
        gap: 6px;
        align-items: flex-start;
      }}
      .category-meta {{
        flex-direction: column;
        align-items: flex-start;
        gap: 4px;
      }}
      .disclosure-tools {{
        align-items: stretch;
      }}
      .tool-group {{
        min-width: 100%;
      }}
      .tool-group.filter-buttons {{
        flex-wrap: wrap;
        gap: 6px;
      }}
      .tool-group.search {{
        min-width: 100%;
      }}
      .tool-group.export {{
        width: 100%;
        flex-wrap: wrap;
      }}
      .tool-group.export button {{
        flex: 1 1 170px;
      }}
      .details-toggle {{
        flex-wrap: wrap;
      }}
      .rec-item {{
        gap: 10px;
      }}
    }}

    @media (max-width: 560px) {{
      body {{
        font-size: 13px;
      }}
      .header-left h1 {{
        font-size: 22px;
      }}
      .score-circle {{
        width: 108px;
        height: 108px;
      }}
      .score-circle .score-num {{
        font-size: 26px;
      }}
      .score-circle .score-label {{
        max-width: 74px;
        font-size: 7px;
      }}
      .section-title {{
        font-size: 20px;
      }}
      .nav-links a {{
        font-size: 11px;
      }}
      .tool-group button,
      .tool-group input[type="text"] {{
        font-size: 12px;
      }}
      .table-scroll {{
        border-radius: 8px;
      }}
    }}

    /* ── Print ── */
    @media print {{
      body {{ background: white; }}
      .stats-bar, .report-header {{ break-inside: avoid; }}
      .category-section {{ break-inside: avoid; }}
      .top-nav, .disclosure-tools, .review-block, .tool-group.export {{ display: none !important; }}
    }}
  </style>
</head>
<body>

<nav class="top-nav">
  <div class="nav-brand">ESRS Gap Detector</div>
  <div class="nav-links">
    <a href="#executive-summary">Summary</a>
    <a href="#statistics">Statistics</a>
    <a href="#improvement-actions">Improvements</a>
    <a href="#disclosure-detail">Disclosures</a>
    <a href="#methodology">Methodology</a>
  </div>
</nav>

<!-- ── Header ── -->
<header class="report-header" id="executive-summary">
  <div class="header-left">
    <div class="label">ESRS Gap Analysis Report</div>
    <h1>{esc(company_name)}</h1>
    <div class="meta">
      <span>📅 {today}</span>
      <span>📋 Framework: {esc(mode_label)} {mode_badge}</span>
      {f'<span class="meta-disclaimer">{esc(mode_header_note)}</span>' if mode_header_note else ''}
      {f'<span>🧭 Profile: {esc(profile_label)}</span>' if profile_label else ''}
      {'<span>📄 Source: ' + esc(pdf_filename) + '</span>' if pdf_filename else ''}
      <span>🔍 {r['total_disclosures']} disclosures analysed</span>
      {llm_meta_html}
    </div>
  </div>
  <div class="score-circle">
    <span class="score-num">{score}</span>
    <span class="score-label">{band['label']}</span>
  </div>
</header>

{materiality_html}

<!-- ── Stats bar ── -->
<div class="stats-bar" id="statistics">
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

{exec_summary_html}

<main class="main-content">
  {sidebar_html}

  <!-- ── Recommendations ── -->
  <section class="recs-section" id="improvement-actions">
    <h2 class="section-title">Top Improvement Actions</h2>
    {rec_html if rec_html else '<p style="color:var(--muted)">No critical improvements identified.</p>'}
  </section>

  <!-- ── Greenwashing flags ── -->
  <section class="flags-section">
    <h2 class="section-title">Greenwashing Risk Signals</h2>
    {qf_html}
  </section>

  <!-- ── Per-category disclosure tables ── -->
  <section id="disclosure-detail">
    <h2 class="section-title">Disclosure Detail</h2>
    {disclosure_tools_html}
    {category_sections_html}
  </section>

</main>

<footer class="report-footer" id="methodology">
  <strong style="color:#94a3b8">ESRS Gap Detector</strong> - Open source tool · github.com/monsieurr/girafon<br>
  ⚠️ This report is advisory only and does not constitute a legal compliance certificate. ESRS rules can evolve and should be validated with a qualified auditor.<br>
  {f'Note: {esc(mode_note)}<br>' if mode_note else ''}
  {f'Profile note: {esc(profile_note)}<br>' if profile_note else ''}
  Framework reference: {framework_ref}
</footer>

<script>
(() => {{
  const rows = Array.from(document.querySelectorAll('.disclosure-row'));
  const statusButtons = Array.from(document.querySelectorAll('[data-filter-status]'));
  const mandatoryOnly = document.getElementById('filter-mandatory');
  const priorityOnly = document.getElementById('filter-priority');
  const searchInput = document.getElementById('filter-search');
  const exportJson = document.getElementById('export-json');
  const exportCsv = document.getElementById('export-csv');
  const expandDetailsBtn = document.getElementById('expand-details');
  const collapseDetailsBtn = document.getElementById('collapse-details');
  const expandSectionsBtn = document.getElementById('expand-sections');
  const collapseSectionsBtn = document.getElementById('collapse-sections');
  const categorySections = Array.from(document.querySelectorAll('.category-section'));
  const resetBtn = document.getElementById('filter-reset');
  const resultCount = document.getElementById('filter-count');

  const storagePrefix = 'girafon_review_';

  const safeGet = (key) => {{
    try {{
      return localStorage.getItem(key);
    }} catch (e) {{
      return null;
    }}
  }};

  const safeSet = (key, value) => {{
    try {{
      localStorage.setItem(key, value);
    }} catch (e) {{
      /* ignore */
    }}
  }};

  let activeStatus = 'all';

  const setActiveStatus = (status) => {{
    activeStatus = status || 'all';
    statusButtons.forEach(btn => {{
      if (btn.getAttribute('data-filter-status') === activeStatus) {{
        btn.classList.add('active');
      }} else {{
        btn.classList.remove('active');
      }}
    }});
  }};

  const updateCount = () => {{
    if (!resultCount) return;
    const visible = rows.filter(row => row.style.display !== 'none').length;
    resultCount.textContent = `${{visible}} of ${{rows.length}}`;
  }};

  const clearHighlights = () => {{
    document.querySelectorAll('mark.search-hit').forEach(mark => {{
      const text = document.createTextNode(mark.textContent || '');
      mark.replaceWith(text);
    }});
  }};

  const highlightTerm = (term) => {{
    if (!term) return;
    const needle = term.toLowerCase();
    const shouldSkip = (el) => {{
      if (!el) return false;
      return el.closest('select, option, button, input, textarea, label');
    }};
    rows.forEach(row => {{
      if (row.style.display === 'none') return;
      const cell = row.querySelector('.col-name');
      if (!cell) return;
      const walker = document.createTreeWalker(
        cell,
        NodeFilter.SHOW_TEXT,
        {{
          acceptNode: (node) => {{
            if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
            if (node.parentElement && node.parentElement.tagName === 'MARK') return NodeFilter.FILTER_REJECT;
            if (shouldSkip(node.parentElement)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
          }}
        }}
      );
      const textNodes = [];
      while (walker.nextNode()) {{
        textNodes.push(walker.currentNode);
      }}
      textNodes.forEach(node => {{
        const text = node.nodeValue || '';
        const lower = text.toLowerCase();
        if (!lower.includes(needle)) return;
        const frag = document.createDocumentFragment();
        let idx = 0;
        while (true) {{
          const i = lower.indexOf(needle, idx);
          if (i === -1) break;
          if (i > idx) {{
            frag.appendChild(document.createTextNode(text.slice(idx, i)));
          }}
          const mark = document.createElement('mark');
          mark.className = 'search-hit';
          mark.textContent = text.slice(i, i + term.length);
          frag.appendChild(mark);
          idx = i + term.length;
        }}
        if (idx < text.length) {{
          frag.appendChild(document.createTextNode(text.slice(idx)));
        }}
        node.parentNode.replaceChild(frag, node);
      }});
    }});
  }};

  const scrollToFirstHit = () => {{
    const first = document.querySelector('mark.search-hit');
    if (first) {{
      first.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}
  }};

  const applyFilters = () => {{
    const statusVal = activeStatus;
    const mustMandatory = mandatoryOnly ? mandatoryOnly.checked : false;
    const mustPriority = priorityOnly ? priorityOnly.checked : false;
    const term = (searchInput && searchInput.value ? searchInput.value : '').toLowerCase().trim();

    rows.forEach(row => {{
      const rowStatus = row.dataset.status || '';
      const rowMandatory = row.dataset.mandatory === 'true';
      const rowPriority = row.dataset.priority === 'high';
      const haystack = row.dataset.search || '';

      let visible = true;
      if (statusVal !== 'all' && rowStatus !== statusVal) visible = false;
      if (mustMandatory && !rowMandatory) visible = false;
      if (mustPriority && !rowPriority) visible = false;
      if (term && !haystack.includes(term)) visible = false;

      row.style.display = visible ? '' : 'none';
    }});
    updateCount();
    clearHighlights();
    if (term.length >= 2) {{
      highlightTerm(term);
      scrollToFirstHit();
    }}
  }};

  const resetFilters = () => {{
    setActiveStatus('all');
    if (mandatoryOnly) mandatoryOnly.checked = false;
    if (priorityOnly) priorityOnly.checked = false;
    if (searchInput) searchInput.value = '';
    applyFilters();
  }};

  const restoreReviewState = () => {{
    rows.forEach(row => {{
      const key = row.dataset.key;
      const select = row.querySelector('.review-select');
      if (!key || !select) return;
      const stored = safeGet(storagePrefix + key);
      if (stored) select.value = stored;
      select.addEventListener('change', () => {{
        safeSet(storagePrefix + key, select.value);
      }});
    }});
  }};

  const getReviewedRows = () => {{
    return rows
      .map(row => {{
        const select = row.querySelector('.review-select');
        const review = select ? select.value : 'to-review';
        return {{
          key: row.dataset.key || '',
          section: row.dataset.section || '',
          name: row.dataset.name || '',
          status: row.dataset.status || '',
          mandatory: row.dataset.mandatory || '',
          priority: row.dataset.priority || '',
          page: row.dataset.page || '',
          quote: row.dataset.quote || '',
          review_status: review,
        }};
      }})
      .filter(item => item.review_status === 'validated' || item.review_status === 'dismissed');
  }};

  const downloadFile = (filename, content, type) => {{
    const blob = new Blob([content], {{ type }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }};

  const toCsv = (items) => {{
    const header = ['key','section','name','status','mandatory','priority','page','quote','review_status'];
    const escape = (v) => {{
      const s = String(v ?? '');
      return '"' + s.replace(/"/g, '""') + '"';
    }};
    const lines = [
      header.join(','),
      ...items.map(item => header.map(k => escape(item[k])).join(','))
    ];
    return lines.join('\\n');
  }};

  const exportReviewedJson = () => {{
    const items = getReviewedRows();
    downloadFile('girafon_reviewed.json', JSON.stringify(items, null, 2), 'application/json');
  }};

  const exportReviewedCsv = () => {{
    const items = getReviewedRows();
    downloadFile('girafon_reviewed.csv', toCsv(items), 'text/csv');
  }};

  if (statusButtons.length) {{
    statusButtons.forEach(btn => {{
      btn.addEventListener('click', () => {{
        setActiveStatus(btn.getAttribute('data-filter-status') || 'all');
        applyFilters();
      }});
    }});
  }}
  if (mandatoryOnly) mandatoryOnly.addEventListener('change', applyFilters);
  if (priorityOnly) priorityOnly.addEventListener('change', applyFilters);
  if (searchInput) searchInput.addEventListener('input', applyFilters);
  if (resetBtn) resetBtn.addEventListener('click', resetFilters);
  if (exportJson) exportJson.addEventListener('click', exportReviewedJson);
  if (exportCsv) exportCsv.addEventListener('click', exportReviewedCsv);
  if (expandDetailsBtn) expandDetailsBtn.addEventListener('click', () => {{
    document.querySelectorAll('.row-details').forEach(d => d.open = true);
  }});
  if (collapseDetailsBtn) collapseDetailsBtn.addEventListener('click', () => {{
    document.querySelectorAll('.row-details').forEach(d => d.open = false);
  }});
  if (expandSectionsBtn) expandSectionsBtn.addEventListener('click', () => {{
    categorySections.forEach(section => section.classList.remove('collapsed'));
  }});
  if (collapseSectionsBtn) collapseSectionsBtn.addEventListener('click', () => {{
    categorySections.forEach(section => section.classList.add('collapsed'));
  }});

  setActiveStatus('all');
  restoreReviewState();
  applyFilters();
}})();
</script>

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
