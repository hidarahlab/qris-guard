"""Step 1 tests: QR decoding, QRIS parsing, and CRC validation."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pytest
import qrcode
from PIL import Image

from app.qris import crc16_ccitt_false, parse_qris
from app.qr import decode_qr


def _make_tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _build_qris_payload(
    *,
    poi: str = "11",
    merchant_name: str = "TOKO MAKMUR",
    merchant_city: str = "JAKARTA",
    country: str = "ID",
    currency: str = "360",
    amount: str | None = None,
    merchant_category: str = "1234",
    include_qris_domain: bool = True,
) -> str:
    """Build a synthetic EMVCo QRIS payload and compute its CRC."""
    gui = "ID.CO.QRIS.WWW" if include_qris_domain else "ID.CO.OTHER.WWW"
    nested_merchant_account = _make_tlv("00", gui)

    payload = ""
    payload += _make_tlv("00", "01")  # payload format indicator
    payload += _make_tlv("01", poi)
    payload += _make_tlv("26", nested_merchant_account)
    payload += _make_tlv("52", merchant_category)
    payload += _make_tlv("53", currency)
    if amount is not None:
        payload += _make_tlv("54", amount)
    payload += _make_tlv("58", country)
    payload += _make_tlv("59", merchant_name)
    payload += _make_tlv("60", merchant_city)

    # Add tag 63 with placeholder CRC; CRC is computed over prefix up to and including "6304".
    prefix = payload + "6304"
    crc = crc16_ccitt_false(prefix.encode("ascii"))
    payload += _make_tlv("63", f"{crc:04X}")
    return payload


# ---------------------------------------------------------------------------
# CRC tests
# ---------------------------------------------------------------------------

def test_crc16_ccitt_false_known_vector():
    """Known test vector for CRC16-CCITT-FALSE."""
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_crc16_ccitt_false_empty():
    assert crc16_ccitt_false(b"") == 0xFFFF


# ---------------------------------------------------------------------------
# parse_qris tests
# ---------------------------------------------------------------------------

def test_parse_qris_valid_static():
    payload = _build_qris_payload(poi="11")
    parsed = parse_qris(payload)

    assert parsed["crc_valid"] is True
    assert parsed["errors"] == []
    assert parsed["type"] == "static"
    assert parsed["merchant_name"] == "TOKO MAKMUR"
    assert parsed["merchant_city"] == "JAKARTA"
    assert parsed["country"] == "ID"
    assert parsed["currency"] == "360"
    assert parsed["merchant_category"] == "1234"
    assert parsed["qris_domain_found"] is True
    assert parsed["amount"] is None


def test_parse_qris_valid_dynamic():
    payload = _build_qris_payload(poi="12", amount="15000")
    parsed = parse_qris(payload)

    assert parsed["crc_valid"] is True
    assert parsed["type"] == "dynamic"
    assert parsed["amount"] == "15000"


def test_parse_qris_invalid_crc():
    payload = _build_qris_payload()
    # Corrupt the final CRC hex value.
    corrupted = payload[:-4] + "0000"
    parsed = parse_qris(corrupted)

    assert parsed["crc_valid"] is False
    assert any("CRC" in err or "crc" in err for err in parsed["errors"]) is False


def test_parse_qris_missing_crc():
    payload = _build_qris_payload()
    without_crc = payload[:-8]  # remove tag 63 entirely
    parsed = parse_qris(without_crc)

    assert parsed["crc_valid"] is False
    assert "Missing CRC tag 63" in parsed["errors"]


def test_parse_qris_malformed_length():
    payload = "0002AB0102"  # tag 00 length 02 but only 2 chars value "AB" then truncated
    parsed = parse_qris(payload)

    assert parsed["crc_valid"] is False
    assert any("length" in err.lower() for err in parsed["errors"])


def test_parse_qris_malformed_non_numeric_length():
    payload = "00XX010211"  # invalid length field
    parsed = parse_qris(payload)

    assert parsed["crc_valid"] is False
    assert any("Invalid length" in err for err in parsed["errors"])


def test_parse_qris_empty():
    parsed = parse_qris("")
    assert parsed["crc_valid"] is False
    assert "Empty payload" in parsed["errors"]


def test_parse_qris_qris_domain_not_found():
    payload = _build_qris_payload(include_qris_domain=False)
    parsed = parse_qris(payload)

    assert parsed["qris_domain_found"] is False


def test_parse_qris_real_sample_like():
    """A realistic-looking synthetic QRIS payload with proper lengths."""
    gui = _make_tlv("00", "ID.CO.QRIS.WWW")
    account = _make_tlv("01", "9360091234567890123")
    merchant_account = _make_tlv("26", gui + account)

    payload = ""
    payload += _make_tlv("00", "01")
    payload += _make_tlv("01", "12")
    payload += merchant_account
    payload += _make_tlv("52", "5411")
    payload += _make_tlv("53", "360")
    payload += _make_tlv("54", "50000")
    payload += _make_tlv("58", "ID")
    payload += _make_tlv("59", "WARUNG MAKAN SEJAHTERA")
    payload += _make_tlv("60", "JAKARTA SELATAN")

    prefix = payload + "6304"
    crc = crc16_ccitt_false(prefix.encode("ascii"))
    payload += _make_tlv("63", f"{crc:04X}")

    parsed = parse_qris(payload)
    assert parsed["crc_valid"] is True
    assert parsed["type"] == "dynamic"
    assert parsed["amount"] == "50000"
    assert parsed["merchant_name"] == "WARUNG MAKAN SEJAHTERA"
    assert parsed["merchant_city"] == "JAKARTA SELATAN"
    assert parsed["qris_domain_found"] is True


# ---------------------------------------------------------------------------
# decode_qr tests
# ---------------------------------------------------------------------------

def _encode_qr(payload: str) -> bytes:
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def sample_qr_payload():
    return _build_qris_payload(merchant_name="QRIS TEST MERCHANT")


@pytest.fixture
def sample_qr_bytes(sample_qr_payload):
    return _encode_qr(sample_qr_payload)


def test_decode_qr_from_bytes(sample_qr_payload, sample_qr_bytes):
    result = decode_qr(sample_qr_bytes)
    assert result.get("payload") == sample_qr_payload
    assert "error" not in result


def test_decode_qr_from_pil(sample_qr_payload, sample_qr_bytes):
    image = Image.open(io.BytesIO(sample_qr_bytes))
    result = decode_qr(image)
    assert result.get("payload") == sample_qr_payload


def test_decode_qr_from_ndarray(sample_qr_payload, sample_qr_bytes):
    image = Image.open(io.BytesIO(sample_qr_bytes))
    arr = np.array(image)  # noqa: F821
    result = decode_qr(arr)
    assert result.get("payload") == sample_qr_payload


def test_decode_qr_not_found():
    # A blank white image has no QR code.
    img = Image.new("RGB", (200, 200), color="white")
    result = decode_qr(img)
    assert "error" in result
    assert "No QR code found" in result["error"]


def test_decode_qr_invalid_input():
    result = decode_qr(12345)  # type: ignore[arg-type]
    assert "error" in result
    assert "Unsupported image type" in result["error"]


def test_decode_qr_blurred_and_rotated(sample_qr_payload):
    """A synthetic QR that is blurred and rotated should still decode."""
    original_bytes = _encode_qr(sample_qr_payload)
    image = Image.open(io.BytesIO(original_bytes)).convert("RGB")
    arr = np.array(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Rotate 15 degrees.
    h, w = gray.shape
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, 15, 1.0)
    rotated = cv2.warpAffine(gray, matrix, (w, h), borderValue=(255,))

    # Add Gaussian blur.
    blurred = cv2.GaussianBlur(rotated, (5, 5), 0)

    result = decode_qr(blurred)
    assert result.get("payload") == sample_qr_payload
    assert "strategy" in result
    assert result["strategy"] != "error"
