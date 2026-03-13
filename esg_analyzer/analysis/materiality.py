from __future__ import annotations

import re
from typing import Dict, List, Optional

from esg_analyzer.parsers.document_parser import ParsedDocument


NON_MATERIAL_RE = re.compile(
    r"\b(non[- ]material|not material|immaterial|not considered material|not deemed material|"
    r"not a material (topic|issue|matter|impact|risk|opportunity))\b",
    re.IGNORECASE,
)

MATERIAL_RE = re.compile(
    r"\b(material (topic|issue|matter|impact|risk|opportunity)|materiality assessment)\b",
    re.IGNORECASE,
)

TOPICS: Dict[str, Dict[str, List[str]]] = {
    "E1": {"keywords": ["climate", "ghg", "greenhouse gas", "emissions", "decarbon", "net zero"]},
    "E2": {"keywords": ["pollution", "air pollut", "water pollut", "soil pollut", "nox", "sox", "voc"]},
    "E3": {"keywords": ["water", "marine", "withdrawal", "consumption", "wastewater", "discharge"]},
    "E4": {"keywords": ["biodiversity", "ecosystem", "nature", "deforestation", "habitat", "land use"]},
    "E5": {"keywords": ["resource", "circular", "waste", "recycling", "materials", "packaging"]},
    "S1": {"keywords": ["workforce", "employees", "staff", "workers", "health and safety", "training", "diversity"]},
    "S2": {"keywords": ["value chain", "supply chain", "suppliers", "contractors"]},
    "S3": {"keywords": ["communities", "community", "indigenous", "local communities", "human rights"]},
    "S4": {"keywords": ["consumers", "customers", "end-users", "product safety", "data privacy"]},
    "G1": {"keywords": ["business conduct", "ethics", "anti-corruption", "bribery", "lobbying", "tax"]},
}

MARK_RE = re.compile(r"\b(x|yes|y|1)\b|[✓✔]", re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"\|")


def _matches_topic(line_lc: str, keywords: List[str]) -> bool:
    return any(kw in line_lc for kw in keywords)

def _parse_table_blocks(lines: List[str]) -> List[List[List[str]]]:
    blocks: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        if TABLE_ROW_RE.search(line) and line.count("|") >= 2:
            current.append(line)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)

    tables: List[List[List[str]]] = []
    for block in blocks:
        rows: List[List[str]] = []
        for line in block:
            stripped = line.strip()
            if not stripped:
                continue
            # skip separator rows like | --- | --- |
            if set(stripped.replace("|", "").strip()) <= {"-"}:
                continue
            parts = [c.strip() for c in stripped.strip("|").split("|")]
            if len(parts) >= 2:
                rows.append(parts)
        if rows:
            tables.append(rows)
    return tables


def _scan_tables_for_materiality(lines: List[str]) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    tables = _parse_table_blocks(lines)
    for rows in tables:
        header_idx = None
        for i, row in enumerate(rows):
            row_lc = " ".join(row).lower()
            if "material" in row_lc:
                header_idx = i
                break
        if header_idx is None:
            continue

        header = [c.lower() for c in rows[header_idx]]
        non_mat_cols = [i for i, c in enumerate(header) if "non material" in c or "immaterial" in c or "not material" in c]
        mat_cols = [i for i, c in enumerate(header) if c.strip() == "material" or "material" in c]
        data_rows = rows[header_idx + 1 :]

        for row in data_rows:
            row_text = " | ".join(row)
            row_lc = row_text.lower()
            for topic, meta in TOPICS.items():
                if not _matches_topic(row_lc, meta["keywords"]):
                    continue
                non_material_hit = False
                for idx in non_mat_cols:
                    if idx < len(row) and MARK_RE.search(row[idx] or ""):
                        non_material_hit = True
                material_hit = False
                for idx in mat_cols:
                    if idx < len(row) and MARK_RE.search(row[idx] or ""):
                        material_hit = True

                if non_material_hit:
                    findings.append({"topic": topic, "status": "non_material", "evidence": row_text})
                elif material_hit:
                    findings.append({"topic": topic, "status": "material", "evidence": row_text})
    return findings


def detect_materiality(doc: ParsedDocument) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Strict-only materiality detection.
    Only returns non_material if the report explicitly states it.
    """
    result: Dict[str, Dict[str, Optional[str]]] = {
        k: {"status": "unknown", "evidence": None, "page": None} for k in TOPICS.keys()
    }

    for chunk in doc.chunks:
        lines = chunk.text.splitlines()

        # 1) Strict parsing of materiality matrices (markdown tables)
        for finding in _scan_tables_for_materiality(lines):
            topic = finding["topic"]
            if result[topic]["status"] == "non_material":
                continue
            status = finding["status"]
            if status == "non_material":
                result[topic] = {
                    "status": "non_material",
                    "evidence": finding["evidence"][:240],
                    "page": str(chunk.page),
                }
            elif result[topic]["status"] == "unknown":
                result[topic] = {
                    "status": "material",
                    "evidence": finding["evidence"][:240],
                    "page": str(chunk.page),
                }

        # 2) Strict line-level parsing
        for line in lines:
            line_clean = " ".join(line.split())
            if not line_clean:
                continue
            line_lc = line_clean.lower()

            for topic, meta in TOPICS.items():
                if not _matches_topic(line_lc, meta["keywords"]):
                    continue

                if NON_MATERIAL_RE.search(line_lc):
                    result[topic] = {
                        "status": "non_material",
                        "evidence": line_clean[:240],
                        "page": str(chunk.page),
                    }
                    continue

                if result[topic]["status"] == "unknown" and MATERIAL_RE.search(line_lc):
                    result[topic] = {
                        "status": "material",
                        "evidence": line_clean[:240],
                        "page": str(chunk.page),
                    }

    return result
