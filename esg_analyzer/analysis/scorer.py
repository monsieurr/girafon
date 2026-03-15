"""
scorer.py
---------
Deterministic weighted scoring engine.
Takes detection results and produces:
  - Per-disclosure scores
  - Category (E/S/G) breakdown
  - Overall weighted score (0–100)
  - Band (Excellent / Good / Needs Improvement / Weak)
  - Top improvement recommendations

All scoring logic is pure Python : no LLM involved.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Status → numeric value ─────────────────────────────────────────────────────

STATUS_VALUES = {
    "FOUND": 1.0,
    "PARTIAL": 0.5,
    "MISSING": 0.0,
}

CATEGORY_ORDER = ["Environment", "Social", "Governance"]

# Required keys that every result dict must contain
_REQUIRED_KEYS = {"key", "section", "name", "status", "category"}


# ── Main scoring function ──────────────────────────────────────────────────────

def compute_scores(
    results: List[Dict[str, Any]],
    scoring_config: Dict[str, Any],
    taxonomy_map: Dict[str, Any] | None = None,
    materiality_map: Dict[str, Any] | None = None,
    mode: str = "original",
) -> Dict[str, Any]:
    """
    Compute the full scoring report from detection results.

    Parameters
    ----------
    results        : List of dicts produced by vars(DetectionResult) in main.py
    scoring_config : _scoring_config block from esrs_schema.json
    mode           : "original" or "omnibus"

    Returns
    -------
    score_report dict with overall_score, band, category breakdown,
    per-item scores, and recommendations.

    Raises
    ------
    ValueError : If results is empty or a result dict is missing required keys.
    """
    if not results:
        raise ValueError("compute_scores received an empty results list.")

    if mode not in ("original", "omnibus"):
        logger.warning("Unknown mode %r : defaulting to 'original'", mode)
        mode = "original"

    weight_key = f"weight_{mode}"

    # Validate that each result has the required keys before touching them
    for i, r in enumerate(results):
        missing = _REQUIRED_KEYS - r.keys()
        if missing:
            raise ValueError(
                f"Result at index {i} (key={r.get('key', '?')!r}) is missing "
                f"required fields: {missing}. "
                f"Pass vars(detection_result) from DetectionResult dataclass."
            )
        # Normalise status defensively : should already be uppercase from detector
        r["status"] = str(r.get("status", "MISSING")).upper()
        if r["status"] not in STATUS_VALUES:
            logger.warning(
                "Result %r has invalid status %r : treating as MISSING",
                r.get("key"), r["status"],
            )
            r["status"] = "MISSING"

    # ── Per-item scoring ───────────────────────────────────────────────────────
    total_weight = 0.0
    weighted_sum = 0.0
    per_item: List[Dict] = []

    for r in results:
        weight = r.get(weight_key, r.get("weight_original", 0))
        status_val = STATUS_VALUES.get(r["status"], 0.0)
        contribution = weight * status_val

        topic_code = _topic_code(r)
        mat_status = "unknown"
        mat_evidence = None
        mat_page = None
        if materiality_map and topic_code in materiality_map:
            mat = materiality_map.get(topic_code) or {}
            mat_status = mat.get("status", "unknown")
            mat_evidence = mat.get("evidence")
            mat_page = mat.get("page")

        exclude_from_scoring = (
            mat_status == "non_material" and not r.get("is_mandatory", False)
        )

        score_weight = 0.0 if exclude_from_scoring else weight
        score_contribution = 0.0 if exclude_from_scoring else contribution
        materiality_badge = "Non-material (explicit)" if mat_status == "non_material" else None

        taxonomy_elements = []
        if taxonomy_map:
            taxonomy_elements = (
                taxonomy_map.get("by_disclosure", {})
                .get(r["key"], {})
                .get("elements", [])
            )

        per_item.append({
            "key": r["key"],
            "section": r["section"],
            "name": r["name"],
            "category": r["category"],
            "pillar": r["pillar"],
            "status": r["status"],
            "weight": weight,
            "score_weight": score_weight,
            "contribution": contribution,
            "score_contribution": score_contribution,
            "best_quote": r.get("best_quote"),
            "page": r.get("page"),
            "reason": r.get("reason"),
            "quality_flags": r.get("quality_flags", []),
            "data_points_found": r.get("data_points_found", []),
            "data_points_missing": r.get("data_points_missing", []),
            "is_mandatory": r.get("is_mandatory", False),
            "omnibus_notes": r.get("omnibus_notes", ""),
            "top_candidate_pages": r.get("top_candidate_pages", []),
            "cross_references": r.get("cross_references", {}),
            "taxonomy_elements": taxonomy_elements,
            "materiality_status": mat_status,
            "materiality_evidence": mat_evidence,
            "materiality_page": mat_page,
            "materiality_badge": materiality_badge,
            "excluded_from_scoring": exclude_from_scoring,
            "ig3": r.get("ig3", {}),
        })

        total_weight += score_weight
        weighted_sum += score_contribution

    # Normalise to 0–100
    overall_score = round((weighted_sum / total_weight) * 100, 1) if total_weight > 0 else 0.0

    # ── Band ───────────────────────────────────────────────────────────────────
    band = _get_band(overall_score, scoring_config.get("bands", {}))

    # ── Category breakdown ─────────────────────────────────────────────────────
    category_scores = _compute_category_scores(per_item, weight_key="score_weight")

    # ── Mandatory compliance rate ──────────────────────────────────────────────
    mandatory_items = [i for i in per_item if i["is_mandatory"]]
    mandatory_found = [i for i in mandatory_items if i["status"] == "FOUND"]
    mandatory_partial = [i for i in mandatory_items if i["status"] == "PARTIAL"]
    mandatory_missing = [i for i in mandatory_items if i["status"] == "MISSING"]

    compliance_rate = (
        round(
            (len(mandatory_found) + 0.5 * len(mandatory_partial)) / len(mandatory_items) * 100, 1
        )
        if mandatory_items else 0.0
    )

    # ── Top recommendations ────────────────────────────────────────────────────
    recommendations = _build_recommendations(per_item)

    # ── Greenwashing flags summary ─────────────────────────────────────────────
    all_quality_flags = []
    for item in per_item:
        for flag in item.get("quality_flags", []):
            all_quality_flags.append({"disclosure": item["name"], "flag": flag})

    return {
        "overall_score": overall_score,
        "band": band,
        "mode": mode,
        "total_disclosures": len(per_item),
        "found_count": sum(1 for i in per_item if i["status"] == "FOUND"),
        "partial_count": sum(1 for i in per_item if i["status"] == "PARTIAL"),
        "missing_count": sum(1 for i in per_item if i["status"] == "MISSING"),
        "mandatory_total": len(mandatory_items),
        "mandatory_found": len(mandatory_found),
        "mandatory_partial": len(mandatory_partial),
        "mandatory_missing": len(mandatory_missing),
        "compliance_rate": compliance_rate,
        "category_scores": category_scores,
        "per_item": per_item,
        "recommendations": recommendations,
        "quality_flags_summary": all_quality_flags,
        "materiality_summary": materiality_map or {},
        "taxonomy_meta": taxonomy_map.get("_meta") if taxonomy_map else None,
    }


def _topic_code(item: Dict[str, Any]) -> Optional[str]:
    ig3 = item.get("ig3", {})
    esrs = str(ig3.get("esrs", "") or "").strip()
    if esrs:
        if esrs.upper().startswith("ESRS 2"):
            return "ESRS 2"
        return esrs

    section = str(item.get("section", "") or "")
    if section:
        prefix = section.split("-")[0].strip()
        if prefix in {"E1", "E2", "E3", "E4", "E5", "S1", "S2", "S3", "S4", "G1"}:
            return prefix

    key = str(item.get("key", "") or "")
    if key:
        prefix = key.split("-")[0].strip()
        if prefix in {"E1", "E2", "E3", "E4", "E5", "S1", "S2", "S3", "S4", "G1"}:
            return prefix
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_band(score: float, bands: Dict) -> Dict:
    for band_key, band in bands.items():
        if band["min"] <= score <= band["max"]:
            return {"key": band_key, "label": band["label"], "color": band["color"]}
    return {"key": "weak", "label": "Weak / High Risk", "color": "#ef4444"}


def _compute_category_scores(
    per_item: List[Dict],
    weight_key: str = "weight",
) -> Dict[str, Dict]:
    categories: Dict[str, Dict] = {}

    for item in per_item:
        cat = item["category"]
        if cat not in categories:
            categories[cat] = {"weighted_sum": 0.0, "total_weight": 0.0, "items": []}

        w = item[weight_key]
        v = STATUS_VALUES.get(item["status"], 0.0)
        categories[cat]["weighted_sum"] += w * v
        categories[cat]["total_weight"] += w
        categories[cat]["items"].append(item)

    result = {}
    for cat, data in categories.items():
        tw = data["total_weight"]
        score = round((data["weighted_sum"] / tw) * 100, 1) if tw > 0 else 0.0
        result[cat] = {
            "score": score,
            "total_items": len(data["items"]),
            "found": sum(1 for i in data["items"] if i["status"] == "FOUND"),
            "partial": sum(1 for i in data["items"] if i["status"] == "PARTIAL"),
            "missing": sum(1 for i in data["items"] if i["status"] == "MISSING"),
        }

    return result


def _build_recommendations(per_item: List[Dict], top_n: int = 5) -> List[Dict]:
    """
    Return the top-N actionable recommendations, prioritised by:
    1. Mandatory disclosures that are MISSING
    2. Mandatory disclosures that are PARTIAL
    3. High-weight optional disclosures that are MISSING
    """
    missing_mandatory = [
        i for i in per_item
        if i["status"] == "MISSING" and i["is_mandatory"] and not i.get("excluded_from_scoring")
    ]
    partial_mandatory = [
        i for i in per_item
        if i["status"] == "PARTIAL" and i["is_mandatory"] and not i.get("excluded_from_scoring")
    ]
    missing_optional = sorted(
        [
            i for i in per_item
            if i["status"] == "MISSING" and not i["is_mandatory"] and not i.get("excluded_from_scoring")
        ],
        key=lambda x: x["weight"],
        reverse=True,
    )

    prioritised = missing_mandatory + partial_mandatory + missing_optional

    recommendations = []
    for item in prioritised[:top_n]:
        missing_dps = item.get("data_points_missing", [])
        if item["status"] == "MISSING":
            action = f"Add {item['name']} disclosure ({item['section']})"
            if missing_dps:
                action += f". Required: {', '.join(missing_dps[:3])}"
        else:
            action = f"Improve {item['name']} ({item['section']})"
            if missing_dps:
                action += f". Missing: {', '.join(missing_dps[:3])}"

        recommendations.append({
            "priority": "HIGH" if item["is_mandatory"] else "MEDIUM",
            "section": item["section"],
            "name": item["name"],
            "status": item["status"],
            "action": action,
            "weight": item["weight"],
        })

    return recommendations
