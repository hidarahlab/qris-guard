#!/usr/bin/env python3
"""Generate synthetic QRIS demo samples and a manifest for the UI.

Usage:
    python scripts/make_samples.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qrcode
from PIL import Image, ImageDraw, ImageFont

from app.qris import crc16_ccitt_false


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "app" / "static" / "samples"
MANIFEST_PATH = ROOT / "app" / "data" / "samples.json"


def _make_tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def build_payload(
    *,
    merchant_name: str,
    merchant_city: str = "JAKARTA DEMO",
    poi: str = "11",
    amount: str | None = None,
    crc_valid: bool = True,
) -> str:
    """Build a synthetic QRIS payload with a valid or invalid CRC."""
    gui = _make_tlv("00", "ID.CO.QRIS.WWW")
    account = _make_tlv("01", "9360000000000001")
    merchant_account = _make_tlv("26", gui + account)

    payload = ""
    payload += _make_tlv("00", "01")
    payload += _make_tlv("01", poi)
    payload += merchant_account
    payload += _make_tlv("52", "1234")
    payload += _make_tlv("53", "360")
    if amount is not None:
        payload += _make_tlv("54", amount)
    payload += _make_tlv("58", "ID")
    payload += _make_tlv("59", merchant_name)
    payload += _make_tlv("60", merchant_city)

    prefix = payload + "6304"
    crc = crc16_ccitt_false(prefix.encode("ascii"))
    if crc_valid:
        payload += _make_tlv("63", f"{crc:04X}")
    else:
        payload += _make_tlv("63", "0000")

    return payload


def _render_qr(payload: str, box_size: int = 12) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def save_qr(payload: str, path: Path) -> None:
    img = _render_qr(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def save_standee(
    payload: str,
    shop_name: str,
    path: Path,
    width: int = 500,
    height: int = 700,
) -> None:
    """Create a standee-style photo: shop name text above a QR code."""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # Try to load a font; fall back to default if unavailable.
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except Exception:
        font = ImageFont.load_default()

    # Draw shop name centered near the top.
    bbox = draw.textbbox((0, 0), shop_name, font=font)
    text_width = bbox[2] - bbox[0]
    text_x = (width - text_width) // 2
    draw.text((text_x, 50), shop_name, fill="black", font=font)

    # Draw QR code below the text, scaled to fit with margins.
    qr = _render_qr(payload, box_size=10)
    max_qr_size = min(width - 80, height - 200)
    qr = qr.resize((max_qr_size, max_qr_size), Image.Resampling.LANCZOS)
    qr_width, qr_height = qr.size
    qr_x = (width - qr_width) // 2
    qr_y = 160
    img.paste(qr, (qr_x, qr_y))

    # Draw a subtle border to look like a printed standee.
    draw.rectangle([10, 10, width - 10, height - 10], outline="#cccccc", width=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    samples = [
        {
            "id": "safe_match",
            "title": {
                "en": "Valid QR - name matches",
                "id": "QR valid - nama cocok"
            },
            "payload_kwargs": {
                "merchant_name": "WARUNG DEMO SEJAHTERA",
                "poi": "11",
            },
            "shop_name_to_enter": "Warung Demo Sejahtera",
            "expected_verdict": "SAFE",
        },
        {
            "id": "mismatch_do_not_pay",
            "title": {
                "en": "Sticker overlay - name mismatch",
                "id": "Stiker tempelan - nama tidak cocok"
            },
            "payload_kwargs": {
                "merchant_name": "TOKO SCAM ABADI",
                "poi": "11",
            },
            "shop_name_to_enter": "Warung Demo Sejahtera",
            "expected_verdict": "DO_NOT_PAY",
        },
        {
            "id": "corrupted_crc_empty_name",
            "title": {
                "en": "Corrupted QR - empty merchant name",
                "id": "QR rusak - nama pedagang kosong"
            },
            "payload_kwargs": {
                "merchant_name": "",
                "poi": "11",
                "crc_valid": False,
            },
            "shop_name_to_enter": "",
            "expected_verdict": "CAUTION",
        },
        {
            "id": "partial_match",
            "title": {
                "en": "Partial name match",
                "id": "Nama cocok sebagian"
            },
            "payload_kwargs": {
                "merchant_name": "WARUNG DEMO SEJAHTERA JAYA",
                "poi": "11",
            },
            "shop_name_to_enter": "Warung Demo Sejahtera",
            "expected_verdict": "CAUTION",
        },
        {
            "id": "standee_vision",
            "title": {
                "en": "Standee photo - read name with vision",
                "id": "Foto standee - baca nama dengan vision"
            },
            "payload_kwargs": {
                "merchant_name": "WARUNG DEMO SEJAHTERA",
                "poi": "11",
            },
            "shop_name_to_enter": "",
            "expected_verdict": "SAFE",
            "standee": True,
        },
    ]

    manifest = []
    for sample in samples:
        payload = build_payload(**sample["payload_kwargs"])
        image_path = OUTPUT_DIR / f"{sample['id']}.png"

        if sample.get("standee"):
            save_standee(payload, sample["payload_kwargs"]["merchant_name"], image_path)
        else:
            save_qr(payload, image_path)

        manifest.append({
            "id": sample["id"],
            "title": sample["title"],
            "image": str(image_path.relative_to(ROOT / "app" / "static")),
            "shop_name_to_enter": sample["shop_name_to_enter"],
            "expected_verdict": sample["expected_verdict"],
        })

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated {len(manifest)} samples in {OUTPUT_DIR}")
    print(f"Manifest written to {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
