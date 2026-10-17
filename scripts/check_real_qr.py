#!/usr/bin/env python3
"""CLI helper to inspect a real QRIS QR code from an image file.

Usage:
    python scripts/check_real_qr.py path/to/qr.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.qr import decode_qr
from app.qris import parse_qris


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode and parse a QRIS QR image.")
    parser.add_argument("image_path", help="Path to the QR image file")
    args = parser.parse_args()

    image_path = Path(args.image_path)
    if not image_path.exists():
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        return 1

    image_bytes = image_path.read_bytes()
    decoded = decode_qr(image_bytes)

    if "error" in decoded:
        print(json.dumps({"decode_error": decoded["error"]}, indent=2))
        return 1

    payload = decoded["payload"]
    parsed = parse_qris(payload)

    output = {
        "payload": payload,
        "strategy": decoded.get("strategy"),
        "merchant_name": parsed.get("merchant_name"),
        "merchant_city": parsed.get("merchant_city"),
        "type": parsed.get("type"),
        "qris_domain_found": parsed.get("qris_domain_found"),
        "crc_valid": parsed.get("crc_valid"),
        "errors": parsed.get("errors"),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
