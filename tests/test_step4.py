"""Step 4 tests: Gemini agent with mocked client and fallback paths."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import qrcode

from app.agent import check_image
from app.qris import crc16_ccitt_false


ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "app" / "static" / "samples"


def _make_tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _build_payload(merchant_name: str, crc_valid: bool = True) -> str:
    gui = _make_tlv("00", "ID.CO.QRIS.WWW")
    account = _make_tlv("01", "9360000000000001")
    merchant_account = _make_tlv("26", gui + account)

    payload = ""
    payload += _make_tlv("00", "01")
    payload += _make_tlv("01", "11")
    payload += merchant_account
    payload += _make_tlv("52", "1234")
    payload += _make_tlv("53", "360")
    payload += _make_tlv("58", "ID")
    payload += _make_tlv("59", merchant_name)
    payload += _make_tlv("60", "JAKARTA DEMO")

    prefix = payload + "6304"
    crc = crc16_ccitt_false(prefix.encode("ascii"))
    if crc_valid:
        payload += _make_tlv("63", f"{crc:04X}")
    else:
        payload += _make_tlv("63", "0000")
    return payload


def _encode_qr(payload: str) -> bytes:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def safe_qr_bytes():
    return _encode_qr(_build_payload("WARUNG DEMO SEJAHTERA"))


@pytest.fixture
def mock_gemini_client():
    """Return a mocked Gemini client that returns deterministic responses."""
    client = MagicMock()

    def generate_content(*, model, contents, config=None):
        # Vision call: contents includes an image (PIL Image) plus prompt string.
        if isinstance(contents, list) and len(contents) == 2 and not isinstance(contents[1], dict):
            response = MagicMock()
            response.text = json.dumps({"observed_shop_name": "Warung Demo Sejahtera"})
            response.function_calls = []
            return response

        # Explanation call: config has system_instruction.
        if config and getattr(config, "system_instruction", None):
            response = MagicMock()
            response.text = "This QR matches the shop name and has a valid checksum, so it looks safe."
            response.function_calls = []
            return response

        # Tool-calling call: return all three tool calls.
        response = MagicMock()
        response.text = ""
        call_parse = MagicMock()
        call_parse.name = "parse_qris"
        call_parse.args = {"payload": contents.split("QRIS payload: ")[1].split("\n")[0]}

        call_match = MagicMock()
        call_match.name = "check_name_match"
        call_match.args = {
            "qr_name": "WARUNG DEMO SEJAHTERA",
            "observed_name": "Warung Demo Sejahtera",
        }

        call_score = MagicMock()
        call_score.name = "risk_score"
        call_score.args = {"parsed": {}, "name_match": {}}

        response.function_calls = [call_parse, call_match, call_score]
        return response

    client.models.generate_content.side_effect = generate_content
    return client


def _patch_env():
    return patch.dict(
        "os.environ",
        {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-test"},
        clear=False,
    )


@patch("app.agent._get_client")
def test_check_image_safe(mock_get_client, safe_qr_bytes, mock_gemini_client):
    mock_get_client.return_value = mock_gemini_client
    with _patch_env():
        result = check_image(safe_qr_bytes, shop_name="Warung Demo Sejahtera", lang="en")

    assert result["verdict"] == "SAFE"
    assert result["score"] == 0
    assert result["observed_shop_name"] == "Warung Demo Sejahtera"
    assert len(result["tool_calls"]) >= 3
    assert any(tc["name"] == "parse_qris" for tc in result["tool_calls"])
    assert any(tc["name"] == "check_name_match" for tc in result["tool_calls"])
    assert any(tc["name"] == "risk_score" for tc in result["tool_calls"])
    assert "explanation" in result


@patch("app.agent._get_client")
def test_check_image_mismatch(mock_get_client, mock_gemini_client):
    payload = _build_payload("TOKO SCAM ABADI")
    qr_bytes = _encode_qr(payload)

    def generate_content(*, model, contents, config=None):
        if config and getattr(config, "system_instruction", None):
            response = MagicMock()
            response.text = "The merchant name does not match the shop name, so do not pay."
            response.function_calls = []
            return response

        response = MagicMock()
        response.text = ""
        response.function_calls = []
        return response

    mock_gemini_client.models.generate_content.side_effect = generate_content
    mock_get_client.return_value = mock_gemini_client

    with _patch_env():
        result = check_image(qr_bytes, shop_name="Warung Demo Sejahtera", lang="en")

    assert result["verdict"] == "DO_NOT_PAY"
    assert result["score"] == 60
    assert any("does not match" in r["en"].lower() for r in result["reasons"])


@patch("app.agent._get_client")
def test_check_image_gemini_failure_falls_back(mock_get_client, safe_qr_bytes):
    """If Gemini API raises, deterministic verdict is still returned with a note."""
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("API timeout")
    mock_get_client.return_value = client

    with _patch_env():
        result = check_image(safe_qr_bytes, shop_name="Warung Demo Sejahtera", lang="en")

    assert result["verdict"] == "SAFE"
    assert result["score"] == 0
    # On API failure the agent falls back to a deterministic template explanation.
    assert result["explanation"]
    assert any("AI explanation service is temporarily unavailable" in r["en"] for r in result["reasons"])


@patch("app.agent._get_client")
def test_check_image_no_shop_name_adds_unverified_reason(mock_get_client, safe_qr_bytes, mock_gemini_client):
    """When no shop name is typed or observed, an extra reason is added without changing score."""

    def generate_content(*, model, contents, config=None):
        if isinstance(contents, list) and len(contents) == 2 and not isinstance(contents[1], dict):
            response = MagicMock()
            response.text = json.dumps({"observed_shop_name": None})
            response.function_calls = []
            return response
        if config and getattr(config, "system_instruction", None):
            response = MagicMock()
            response.text = "No shop name was provided, so the check is based on QR data alone."
            response.function_calls = []
            return response
        response = MagicMock()
        response.text = ""
        response.function_calls = []
        return response

    mock_gemini_client.models.generate_content.side_effect = generate_content
    mock_get_client.return_value = mock_gemini_client

    with _patch_env():
        result = check_image(safe_qr_bytes, lang="en")

    assert result["verdict"] == "SAFE"
    assert result["score"] == 0
    assert any("Shop name was not verified" in r["en"] for r in result["reasons"])


@patch("app.agent._get_client")
def test_check_image_vision_reads_name(mock_get_client, safe_qr_bytes, mock_gemini_client):
    """Vision-derived observed name is used when no shop_name is typed."""

    def generate_content(*, model, contents, config=None):
        if isinstance(contents, list) and len(contents) == 2 and not isinstance(contents[1], dict):
            response = MagicMock()
            response.text = json.dumps({"observed_shop_name": "Warung Demo Sejahtera"})
            response.function_calls = []
            return response
        if config and getattr(config, "system_instruction", None):
            response = MagicMock()
            response.text = "Looks safe."
            response.function_calls = []
            return response
        response = MagicMock()
        response.text = ""
        response.function_calls = []
        return response

    mock_gemini_client.models.generate_content.side_effect = generate_content
    mock_get_client.return_value = mock_gemini_client

    with _patch_env():
        result = check_image(safe_qr_bytes, lang="en")

    assert result["observed_shop_name"] == "Warung Demo Sejahtera"
    assert result["verdict"] == "SAFE"


def test_check_image_no_qr_code():
    """An image with no QR returns DO_NOT_PAY."""
    from PIL import Image

    img = Image.new("RGB", (200, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    with _patch_env():
        result = check_image(buf.getvalue(), lang="en")

    assert result["verdict"] == "DO_NOT_PAY"
    assert result["score"] == 100


@patch("app.agent._get_client")
def test_check_image_with_demo_samples(mock_get_client, mock_gemini_client):
    """Run the agent against the generated demo samples and assert expected verdicts."""
    manifest_path = ROOT / "app" / "data" / "samples.json"
    samples = json.loads(manifest_path.read_text(encoding="utf-8"))

    for sample in samples:
        image_path = SAMPLES_DIR / f"{sample['id']}.png"
        if not image_path.exists():
            continue

        def make_gen(sample_name=sample["shop_name_to_enter"], sample_verdict=sample["expected_verdict"]):
            def generate_content(*, model, contents, config=None):
                if isinstance(contents, list) and len(contents) == 2 and not isinstance(contents[1], dict):
                    response = MagicMock()
                    response.text = json.dumps({"observed_shop_name": sample_name or None})
                    response.function_calls = []
                    return response
                if config and getattr(config, "system_instruction", None):
                    response = MagicMock()
                    response.text = f"Expected verdict: {sample_verdict}."
                    response.function_calls = []
                    return response
                response = MagicMock()
                response.text = ""
                response.function_calls = []
                return response
            return generate_content

        mock_gemini_client.models.generate_content.side_effect = make_gen()
        mock_get_client.return_value = mock_gemini_client

        with _patch_env():
            result = check_image(image_path.read_bytes(), shop_name=sample["shop_name_to_enter"] or None, lang="en")

        assert result["verdict"] == sample["expected_verdict"], f"{sample['id']} failed"
