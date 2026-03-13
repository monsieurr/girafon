from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_taxonomy_map(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "by_disclosure" not in data:
        return None
    return data


def elements_for_disclosure(
    taxonomy_map: Optional[Dict[str, Any]],
    disclosure_key: str,
) -> List[Dict[str, Any]]:
    if not taxonomy_map:
        return []
    return taxonomy_map.get("by_disclosure", {}).get(disclosure_key, {}).get("elements", [])
