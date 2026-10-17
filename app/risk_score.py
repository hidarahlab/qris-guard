"""Deterministic risk scoring for QRIS Guard."""

from __future__ import annotations

import json
from pathlib import Path


_CONFIG_DIR = Path(__file__).resolve().parent / "config"
_DATA_DIR = Path(__file__).resolve().parent / "data"

_REASONS = {
    "name_mismatch": {
        "en": "Merchant name does not match the shop name you see.",
        "id": "Nama pedagang tidak cocok dengan nama toko yang Anda lihat."
    },
    "name_partial": {
        "en": "Merchant name only partially matches the shop name.",
        "id": "Nama pedagang hanya cocok sebagian dengan nama toko."
    },
    "crc_invalid": {
        "en": "QR code checksum is invalid; data may have been altered.",
        "id": "Checksum kode QR tidak valid; data mungkin telah diubah."
    },
    "merchant_name_missing": {
        "en": "Merchant name is missing from the QR code.",
        "id": "Nama pedagang tidak ada di kode QR."
    },
    "qris_domain_missing": {
        "en": "QR code does not contain a recognized QRIS identifier.",
        "id": "Kode QR tidak memiliki identitas QRIS yang dikenali."
    },
    "fraud_pattern_match": {
        "en": "QR matches a known suspicious pattern.",
        "id": "QR cocok dengan pola mencurigakan yang diketahui."
    }
}


def _load_json(path: Path) -> dict | list:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_scoring_config() -> dict:
    raw = _load_json(_CONFIG_DIR / "scoring.json")
    return raw if isinstance(raw, dict) else {}


def _verdict_and_label(score: int, config: dict) -> tuple[str, str]:
    thresholds = config.get("thresholds", {})
    labels = config.get("labels", {})
    if score <= thresholds.get("safe_max", 24):
        return "SAFE", labels.get("safe", "No red flags detected")
    if score <= thresholds.get("caution_max", 54):
        return "CAUTION", labels.get("caution", "Caution")
    return "DO_NOT_PAY", labels.get("do_not_pay", "Do not pay")


def risk_score(
    parsed: dict,
    name_match: dict,
    matched_patterns: list[str] | None = None
) -> dict:
    """Compute risk score and verdict from parsed QRIS data and name match.

    Returns {
        "score": int,
        "verdict": str,
        "verdict_label": str,
        "reasons": [{"en": str, "id": str}],
        "matched_patterns": [str]
    }.
    """
    config = _load_scoring_config()
    signals = config.get("signals", {})
    reasons: list = []
    score = 0
    matched = list(matched_patterns or [])

    match_status = name_match.get("match")
    if match_status == "mismatch":
        score += signals.get("name_mismatch", 60)
        reasons.append(_REASONS["name_mismatch"])
    elif match_status == "partial":
        score += signals.get("name_partial", 25)
        reasons.append(_REASONS["name_partial"])

    if parsed.get("crc_valid") is False:
        score += signals.get("crc_invalid", 25)
        reasons.append(_REASONS["crc_invalid"])

    if not parsed.get("merchant_name"):
        score += signals.get("merchant_name_missing", 20)
        reasons.append(_REASONS["merchant_name_missing"])

    if parsed.get("qris_domain_found") is False:
        score += signals.get("qris_domain_missing", 30)
        reasons.append(_REASONS["qris_domain_missing"])

    if matched:
        score += signals.get("fraud_pattern_match", 70) * len(matched)
        reasons.append(_REASONS["fraud_pattern_match"])

    verdict, label = _verdict_and_label(score, config)

    return {
        "score": score,
        "verdict": verdict,
        "verdict_label": label,
        "reasons": reasons,
        "matched_patterns": matched
    }
