"""Name matching between QR merchant name and the shop name observed by the user."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path


_CONFIG_DIR = Path(__file__).resolve().parent / "config"


def _load_json(filename: str) -> dict:
    path = _CONFIG_DIR / filename
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _normalize(name: str) -> str:
    """Normalize a merchant/shop name for comparison.

    Steps:
      1. Lowercase and replace punctuation with spaces.
      2. Strip leading numeric codes (e.g. '082 ').
      3. Expand common abbreviations from abbreviations.json.
      4. Drop legal/commercial prefixes (PT, CV, TOKO, WARUNG, etc.).
      5. Collapse whitespace.
    """
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"^\d+\s+", "", name)

    abbreviations = _load_json("abbreviations.json")
    tokens = name.split()
    expanded = [abbreviations.get(token, token) for token in tokens]

    prefixes = {"pt", "cv", "toko", "warung", "kantor", "ruko", "minimarket", "umkm"}
    filtered = [token for token in expanded if token not in prefixes]

    return re.sub(r"\s+", " ", " ".join(filtered)).strip()


def check_name_match(qr_name: str | None, observed_name: str | None) -> dict:
    """Compare QR merchant name with the observed shop name.

    Returns {"match": "exact|partial|mismatch|unknown", "similarity": float}.
    """
    if not qr_name or not observed_name:
        return {"match": "unknown", "similarity": 0.0}

    norm_qr = _normalize(qr_name)
    norm_obs = _normalize(observed_name)

    if not norm_qr or not norm_obs:
        return {"match": "unknown", "similarity": 0.0}

    if norm_qr == norm_obs:
        return {"match": "exact", "similarity": 1.0}

    char_sim = SequenceMatcher(None, norm_qr, norm_obs).ratio()

    qr_tokens = set(norm_qr.split())
    obs_tokens = set(norm_obs.split())
    if qr_tokens and obs_tokens:
        overlap = len(qr_tokens & obs_tokens) / min(len(qr_tokens), len(obs_tokens))
    else:
        overlap = 0.0

    similarity = max(char_sim, overlap * 0.9)

    config = _load_json("scoring.json")
    partial_threshold = config.get("name_match", {}).get("partial_threshold", 0.5)

    if similarity >= partial_threshold:
        return {"match": "partial", "similarity": round(similarity, 3)}

    return {"match": "mismatch", "similarity": round(similarity, 3)}
