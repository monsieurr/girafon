"""
diff.py
-------
Compute a comparison (diff) between two score_report outputs.
Keeps logic small and safe, without touching the core pipeline.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


_STATUS_ORDER = {"MISSING": 0, "PARTIAL": 1, "FOUND": 2}


def _normalize_status(value: Any) -> str:
    status = str(value or "MISSING").upper()
    return status if status in _STATUS_ORDER else "MISSING"


def _priority_gaps(report: Dict[str, Any]) -> int:
    return sum(1 for rec in report.get("recommendations", []) if rec.get("priority") == "HIGH")


def _items_by_key(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("key")): item for item in report.get("per_item", []) if item.get("key")}


def _status_delta(base_status: str, new_status: str) -> Tuple[int, str]:
    base_val = _STATUS_ORDER.get(base_status, 0)
    new_val = _STATUS_ORDER.get(new_status, 0)
    delta = new_val - base_val
    if delta > 0:
        return delta, "improved"
    if delta < 0:
        return delta, "regressed"
    return 0, "unchanged"


def compute_diff_report(
    base_report: Dict[str, Any],
    new_report: Dict[str, Any],
    *,
    base_label: str = "Baseline",
    new_label: str = "Comparison",
    base_report_html: str | None = None,
    new_report_html: str | None = None,
) -> Dict[str, Any]:
    """
    Compare two score_report outputs and return a diff_report dict.
    """
    base_items = _items_by_key(base_report)
    new_items = _items_by_key(new_report)
    keys = sorted(set(base_items.keys()) | set(new_items.keys()))

    items: List[Dict[str, Any]] = []
    improved = regressed = unchanged = 0
    still_missing = still_missing_mandatory = 0
    new_missing = closed_gaps = 0
    regressed_mandatory = 0

    for key in keys:
        b = base_items.get(key, {})
        n = new_items.get(key, {})

        section = n.get("section") or b.get("section") or ""
        name = n.get("name") or b.get("name") or ""
        weight = n.get("weight", b.get("weight", 0))
        mandatory = bool(n.get("is_mandatory", b.get("is_mandatory", False)))

        base_status = _normalize_status(b.get("status"))
        new_status = _normalize_status(n.get("status"))
        delta, change_type = _status_delta(base_status, new_status)

        if change_type == "improved":
            improved += 1
        elif change_type == "regressed":
            regressed += 1
            if mandatory:
                regressed_mandatory += 1
        else:
            unchanged += 1

        if base_status in ("MISSING", "PARTIAL") and new_status == "FOUND":
            closed_gaps += 1
        if base_status in ("FOUND", "PARTIAL") and new_status == "MISSING":
            new_missing += 1
        if base_status in ("MISSING", "PARTIAL") and new_status in ("MISSING", "PARTIAL"):
            still_missing += 1
            if mandatory:
                still_missing_mandatory += 1

        items.append(
            {
                "key": key,
                "section": section,
                "name": name,
                "weight": weight,
                "mandatory": mandatory,
                "base_status": base_status,
                "new_status": new_status,
                "change_type": change_type,
                "transition": f"{base_status} → {new_status}",
            }
        )

    summary = {
        "base": {
            "label": base_label,
            "overall_score": base_report.get("overall_score"),
            "mandatory_compliance": base_report.get("compliance_rate"),
            "mandatory_missing": base_report.get("mandatory_missing"),
            "high_priority_gaps": _priority_gaps(base_report),
            "greenwashing_flags": len(base_report.get("quality_flags_summary", [])),
            "report_html": base_report_html,
        },
        "new": {
            "label": new_label,
            "overall_score": new_report.get("overall_score"),
            "mandatory_compliance": new_report.get("compliance_rate"),
            "mandatory_missing": new_report.get("mandatory_missing"),
            "high_priority_gaps": _priority_gaps(new_report),
            "greenwashing_flags": len(new_report.get("quality_flags_summary", [])),
            "report_html": new_report_html,
        },
        "deltas": {
            "overall_score": (new_report.get("overall_score", 0) or 0) - (base_report.get("overall_score", 0) or 0),
            "mandatory_compliance": (new_report.get("compliance_rate", 0) or 0) - (base_report.get("compliance_rate", 0) or 0),
            "mandatory_missing": (new_report.get("mandatory_missing", 0) or 0) - (base_report.get("mandatory_missing", 0) or 0),
            "high_priority_gaps": _priority_gaps(new_report) - _priority_gaps(base_report),
            "greenwashing_flags": len(new_report.get("quality_flags_summary", [])) - len(base_report.get("quality_flags_summary", [])),
        },
    }

    return {
        "summary": summary,
        "items": items,
        "counts": {
            "improved": improved,
            "regressed": regressed,
            "unchanged": unchanged,
            "still_missing": still_missing,
            "still_missing_mandatory": still_missing_mandatory,
            "new_missing": new_missing,
            "closed_gaps": closed_gaps,
            "regressed_mandatory": regressed_mandatory,
        },
    }
