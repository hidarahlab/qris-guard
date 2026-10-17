"""Step 2 tests: name matching, risk scoring, and fraud patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.name_match import check_name_match
from app.risk_score import risk_score


@pytest.fixture
def fraud_patterns():
    path = Path(__file__).resolve().parent.parent / "app" / "data" / "fraud_patterns.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# check_name_match tests
# ---------------------------------------------------------------------------

def test_name_match_exact():
    result = check_name_match("Warung Makmur", "warung makmur")
    assert result["match"] == "exact"
    assert result["similarity"] == 1.0


def test_name_match_partial():
    result = check_name_match("Toko Sumber Rejeki", "Sumber Rejeki Jaya")
    assert result["match"] == "partial"
    assert result["similarity"] > 0


def test_name_match_mismatch():
    result = check_name_match("Toko A", "Toko B")
    assert result["match"] == "mismatch"


def test_name_match_unknown():
    assert check_name_match("", "Something")["match"] == "unknown"
    assert check_name_match("Something", None)["match"] == "unknown"
    assert check_name_match(None, None)["match"] == "unknown"


def test_name_match_drops_legal_prefixes():
    result = check_name_match("PT Sumber Jaya", "Sumber Jaya")
    assert result["match"] == "exact"


def test_name_match_strips_numeric_code_and_expands_abbreviation():
    result = check_name_match("082 PUSK DLANGGU", "Puskesmas Dlanggu")
    assert result["match"] != "mismatch"
    assert result["match"] in ("exact", "partial")


def test_name_match_abbreviation_vs_unrelated_is_mismatch():
    result = check_name_match("082 PUSK DLANGGU", "Toko Sumber Rejeki")
    assert result["match"] == "mismatch"


def test_name_match_expands_wrg_and_tk():
    assert check_name_match("WRG Barokah", "Warung Barokah")["match"] == "exact"
    assert check_name_match("TK Sejahtera", "Toko Sejahtera")["match"] == "exact"


# ---------------------------------------------------------------------------
# risk_score tests
# ---------------------------------------------------------------------------

def _parsed(
    *,
    merchant_name: str | None = "Test Merchant",
    crc_valid: bool = True,
    qris_domain_found: bool = True
) -> dict:
    return {
        "merchant_name": merchant_name,
        "crc_valid": crc_valid,
        "qris_domain_found": qris_domain_found,
    }


def test_scenario_safe():
    parsed = _parsed(merchant_name="Test Merchant")
    name_match = check_name_match("Test Merchant", "Test Merchant")
    result = risk_score(parsed, name_match)
    assert result["score"] == 0
    assert result["verdict"] == "SAFE"
    assert result["reasons"] == []


def test_scenario_mismatch_do_not_pay():
    parsed = _parsed(merchant_name="Scam Account")
    name_match = check_name_match("Scam Account", "Real Shop Name")
    result = risk_score(parsed, name_match)
    assert result["score"] == 60
    assert result["verdict"] == "DO_NOT_PAY"
    assert any("Merchant name does not match" in r["en"] for r in result["reasons"])


def test_scenario_corrupted_crc_and_empty_name_caution_score_45():
    parsed = _parsed(merchant_name=None, crc_valid=False)
    name_match = check_name_match(None, "Any Shop")
    result = risk_score(parsed, name_match)
    assert result["score"] == 45
    assert result["verdict"] == "CAUTION"
    assert any("checksum" in r["en"].lower() for r in result["reasons"])
    assert any("missing" in r["en"].lower() for r in result["reasons"])


def test_scenario_partial_match_caution():
    parsed = _parsed(merchant_name="Sumber Rejeki")
    name_match = check_name_match("Sumber Rejeki", "Sumber Rejeki Jaya")
    result = risk_score(parsed, name_match)
    assert result["score"] == 25
    assert result["verdict"] == "CAUTION"
    assert any("partially matches" in r["en"].lower() for r in result["reasons"])


def test_risk_score_qris_domain_missing():
    parsed = _parsed(qris_domain_found=False)
    name_match = check_name_match("Test Merchant", "Test Merchant")
    result = risk_score(parsed, name_match)
    assert result["score"] == 30
    assert result["verdict"] == "CAUTION"


def test_risk_score_fraud_pattern_match():
    parsed = _parsed()
    name_match = check_name_match("Test Merchant", "Test Merchant")
    result = risk_score(parsed, name_match, matched_patterns=["sticker_overlay"])
    assert result["score"] == 70
    assert result["verdict"] == "DO_NOT_PAY"
    assert "sticker_overlay" in result["matched_patterns"]


# ---------------------------------------------------------------------------
# Data file tests
# ---------------------------------------------------------------------------

def test_fraud_patterns_count(fraud_patterns):
    assert 8 <= len(fraud_patterns) <= 10


def test_fraud_patterns_have_required_fields(fraud_patterns):
    for pattern in fraud_patterns:
        assert "id" in pattern
        assert "title" in pattern
        assert "description" in pattern
        assert "signals" in pattern
        assert "advice" in pattern
        assert "en" in pattern["advice"]
        assert "id" in pattern["advice"]
