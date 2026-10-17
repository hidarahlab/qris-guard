"""Step 3 tests: synthetic demo samples decode and score as expected."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.name_match import check_name_match
from app.qr import decode_qr
from app.qris import parse_qris
from app.risk_score import risk_score


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "app" / "data" / "samples.json"
SAMPLES_DIR = ROOT / "app" / "static" / "samples"


@pytest.fixture(scope="module")
def manifest():
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_exists_and_has_samples(manifest):
    assert len(manifest) == 5
    for sample in manifest:
        assert "id" in sample
        assert "title" in sample
        assert "image" in sample
        assert "shop_name_to_enter" in sample
        assert "expected_verdict" in sample
        assert Path(SAMPLES_DIR / f"{sample['id']}.png").exists()


@pytest.mark.parametrize("sample", json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
def test_sample_end_to_end(sample):
    """Decode each sample QR and verify the full pipeline produces the expected verdict."""
    image_path = SAMPLES_DIR / f"{sample['id']}.png"
    image_bytes = image_path.read_bytes()

    decoded = decode_qr(image_bytes)
    assert "payload" in decoded, f"Decode failed for {sample['id']}: {decoded.get('error')}"

    parsed = parse_qris(decoded["payload"])
    name_match = check_name_match(parsed.get("merchant_name"), sample["shop_name_to_enter"] or None)
    scored = risk_score(parsed, name_match)

    assert scored["verdict"] == sample["expected_verdict"], (
        f"{sample['id']}: expected {sample['expected_verdict']}, "
        f"got {scored['verdict']} (score={scored['score']}, "
        f"merchant={parsed.get('merchant_name')}, match={name_match})"
    )


def test_sample_safe_match_has_zero_score():
    sample = next(s for s in json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if s["id"] == "safe_match")
    image_bytes = (SAMPLES_DIR / f"{sample['id']}.png").read_bytes()

    decoded = decode_qr(image_bytes)
    parsed = parse_qris(decoded["payload"])
    name_match = check_name_match(parsed.get("merchant_name"), sample["shop_name_to_enter"])
    scored = risk_score(parsed, name_match)

    assert scored["score"] == 0
    assert scored["verdict"] == "SAFE"


def test_sample_corrupted_crc_empty_name_score_45():
    sample = next(
        s for s in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if s["id"] == "corrupted_crc_empty_name"
    )
    image_bytes = (SAMPLES_DIR / f"{sample['id']}.png").read_bytes()

    decoded = decode_qr(image_bytes)
    parsed = parse_qris(decoded["payload"])
    name_match = check_name_match(parsed.get("merchant_name"), sample["shop_name_to_enter"] or None)
    scored = risk_score(parsed, name_match)

    assert scored["score"] == 45
    assert scored["verdict"] == "CAUTION"
