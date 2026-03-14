"""
diff_report.py
--------------
Generate a standalone HTML diff report from a diff_report dict.
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional


def generate_diff_report(diff_report: Dict[str, Any], output_path: Optional[str] = None) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def esc_attr(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    summary = diff_report.get("summary", {})
    base = summary.get("base", {})
    new = summary.get("new", {})
    deltas = summary.get("deltas", {})
    counts = diff_report.get("counts", {})
    items = diff_report.get("items", [])

    def delta_badge(value: Any) -> str:
        try:
            v = float(value)
        except Exception:
            v = 0.0
        if v > 0:
            return f'<span class="delta up">+{esc(value)}</span>'
        if v < 0:
            return f'<span class="delta down">{esc(value)}</span>'
        return f'<span class="delta flat">0</span>'

    rows_html = ""
    for item in items:
        rows_html += f"""
        <tr class="diff-row change-{esc_attr(item.get('change_type', 'unchanged'))}"
            data-change="{esc_attr(item.get('change_type', 'unchanged'))}"
            data-mandatory="{esc_attr(str(item.get('mandatory', False)).lower())}"
            data-search="{esc_attr(str(item.get('section', '')) + ' ' + str(item.get('name', '')))}">
          <td class="col-section">{esc(item.get("section", ""))}</td>
          <td class="col-name">{esc(item.get("name", ""))}</td>
          <td class="col-status">{esc(item.get("base_status", ""))}</td>
          <td class="col-status">{esc(item.get("new_status", ""))}</td>
          <td class="col-change">{esc(item.get("transition", ""))}</td>
          <td class="col-weight">{esc(item.get("weight", ""))}</td>
          <td class="col-mandatory">{'Yes' if item.get('mandatory') else 'No'}</td>
        </tr>"""

    today = date.today().strftime("%d %B %Y")
    base_link = base.get("report_html") or ""
    new_link = new.get("report_html") or ""
    base_link_html = (
        f'<a class="link" href="{esc_attr(base_link)}">{esc(base_link)}</a>'
        if base_link
        else "-"
    )
    new_link_html = (
        f'<a class="link" href="{esc_attr(new_link)}">{esc(new_link)}</a>'
        if new_link
        else "-"
    )
    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Girafon — Report Diff</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --navy: #0d1b2a;
      --ink: #1e293b;
      --muted: #64748b;
      --border: #e2e8f0;
      --bg: #f8fafc;
      --found: #22c55e;
      --partial: #f59e0b;
      --missing: #ef4444;
      --accent: #3b82f6;
    }}
    body {{
      font-family: 'DM Sans', sans-serif;
      font-size: 14px;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.6;
    }}
    .header {{
      background: var(--navy);
      color: white;
      padding: 28px 48px;
      display: flex;
      justify-content: space-between;
      gap: 20px;
      flex-wrap: wrap;
    }}
    .header h1 {{
      font-family: 'DM Serif Display', serif;
      font-size: 26px;
      font-weight: 400;
    }}
    .header .meta {{
      font-size: 12px;
      color: #cbd5f5;
      display: grid;
      gap: 6px;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 48px 60px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: white;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .card h3 {{
      font-family: 'DM Serif Display', serif;
      font-size: 16px;
      font-weight: 400;
      margin-bottom: 6px;
    }}
    .delta {{
      font-size: 12px;
      font-weight: 700;
      margin-left: 6px;
    }}
    .delta.up {{ color: #166534; }}
    .delta.down {{ color: #991b1b; }}
    .delta.flat {{ color: #64748b; }}
    .link {{
      font-size: 11px;
      color: #dbeafe;
      text-decoration: none;
    }}
    .tools {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
      background: white;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 16px;
      margin-bottom: 16px;
    }}
    .tool {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
    }}
    .tool label {{
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .tool input, .tool select, .tool button {{
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 13px;
      color: var(--ink);
      background: #fff;
    }}
    .tool button {{
      background: #f8fafc;
      cursor: pointer;
      font-weight: 600;
    }}
    .tool input:focus, .tool select:focus, .tool button:focus {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }}
    thead {{
      background: #f1f5f9;
    }}
    th, td {{
      padding: 10px 12px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }}
    th {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .change-improved {{ background: #f0fdf4; }}
    .change-regressed {{ background: #fef2f2; }}
    .change-unchanged {{ background: #ffffff; }}
    .footer {{
      margin-top: 24px;
      font-size: 11px;
      color: var(--muted);
    }}
    @media print {{
      body {{ background: white; }}
      .tools {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>Girafon — Report Diff</h1>
      <div class="meta">
        <div>{esc(base.get("label", "Baseline"))} → {esc(new.get("label", "Comparison"))}</div>
        <div>{esc(today)}</div>
      </div>
    </div>
    <div class="meta">
      <div>Baseline report: {base_link_html}</div>
      <div>Comparison report: {new_link_html}</div>
    </div>
  </div>

  <div class="wrap">
    <div class="summary-grid">
      <div class="card">
        <h3>Overall score</h3>
        <div>{esc(base.get("overall_score"))} → {esc(new.get("overall_score"))} {delta_badge(deltas.get("overall_score"))}</div>
      </div>
      <div class="card">
        <h3>Mandatory compliance</h3>
        <div>{esc(base.get("mandatory_compliance"))}% → {esc(new.get("mandatory_compliance"))}% {delta_badge(deltas.get("mandatory_compliance"))}</div>
      </div>
      <div class="card">
        <h3>Mandatory missing</h3>
        <div>{esc(base.get("mandatory_missing"))} → {esc(new.get("mandatory_missing"))} {delta_badge(deltas.get("mandatory_missing"))}</div>
      </div>
      <div class="card">
        <h3>High priority gaps</h3>
        <div>{esc(base.get("high_priority_gaps"))} → {esc(new.get("high_priority_gaps"))} {delta_badge(deltas.get("high_priority_gaps"))}</div>
      </div>
      <div class="card">
        <h3>Greenwashing flags</h3>
        <div>{esc(base.get("greenwashing_flags"))} → {esc(new.get("greenwashing_flags"))} {delta_badge(deltas.get("greenwashing_flags"))}</div>
      </div>
      <div class="card">
        <h3>Summary</h3>
        <div>Improved: {esc(counts.get("improved", 0))}</div>
        <div>Regressed: {esc(counts.get("regressed", 0))}</div>
        <div>Still missing (mandatory): {esc(counts.get("still_missing_mandatory", 0))}</div>
      </div>
    </div>

    <div class="tools">
      <div class="tool">
        <label for="filter-change">Change</label>
        <select id="filter-change">
          <option value="all">All</option>
          <option value="improved">Improved</option>
          <option value="regressed">Regressed</option>
          <option value="unchanged">Unchanged</option>
        </select>
      </div>
      <div class="tool">
        <label><input type="checkbox" id="filter-mandatory"> Mandatory only</label>
      </div>
      <div class="tool">
        <label for="filter-search">Search</label>
        <input type="text" id="filter-search" placeholder="Section or disclosure" aria-label="Search disclosures">
      </div>
      <div class="tool">
        <label for="filter-reset">Reset</label>
        <button id="filter-reset" type="button">Reset</button>
      </div>
      <div class="tool">
        <label>Results</label>
        <div id="filter-count">0 of 0</div>
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th>Section</th>
          <th>Disclosure</th>
          <th>Before</th>
          <th>After</th>
          <th>Change</th>
          <th>Weight</th>
          <th>Mandatory</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    <div class="footer">
      This diff is a remediation support view, not an ESG benchmark.
    </div>
  </div>

  <script>
  (() => {{
    const rows = Array.from(document.querySelectorAll('.diff-row'));
    const changeSelect = document.getElementById('filter-change');
    const mandatoryOnly = document.getElementById('filter-mandatory');
    const searchInput = document.getElementById('filter-search');
    const resetBtn = document.getElementById('filter-reset');
    const countEl = document.getElementById('filter-count');

    const applyFilters = () => {{
      const change = changeSelect ? changeSelect.value : 'all';
      const mustMandatory = mandatoryOnly ? mandatoryOnly.checked : false;
      const term = (searchInput && searchInput.value ? searchInput.value : '').toLowerCase().trim();

      rows.forEach(row => {{
        const rowChange = row.dataset.change || '';
        const rowMandatory = row.dataset.mandatory === 'true';
        const haystack = row.dataset.search || '';

        let visible = true;
        if (change !== 'all' && rowChange !== change) visible = false;
        if (mustMandatory && !rowMandatory) visible = false;
        if (term && !haystack.toLowerCase().includes(term)) visible = false;

        row.style.display = visible ? '' : 'none';
      }});
      if (countEl) {{
        const visible = rows.filter(row => row.style.display !== 'none').length;
        countEl.textContent = `${{visible}} of ${{rows.length}}`;
      }}
    }};

    const resetFilters = () => {{
      if (changeSelect) changeSelect.value = 'all';
      if (mandatoryOnly) mandatoryOnly.checked = false;
      if (searchInput) searchInput.value = '';
      applyFilters();
    }};

    if (changeSelect) changeSelect.addEventListener('change', applyFilters);
    if (mandatoryOnly) mandatoryOnly.addEventListener('change', applyFilters);
    if (searchInput) searchInput.addEventListener('input', applyFilters);
    if (resetBtn) resetBtn.addEventListener('click', resetFilters);
    applyFilters();
  }})();
  </script>
</body>
</html>"""

    if output_path:
        Path(output_path).write_text(report_html, encoding="utf-8")
    return report_html
