#!/usr/bin/env python3
"""Smoke-test the Gemini agent against the 5 generated demo samples.

Usage:
    export GEMINI_API_KEY=...
    export GEMINI_MODEL=gemini-3.8-flash
    python scripts/smoke_agent.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import check_image


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "app" / "data" / "samples.json"
SAMPLES_DIR = ROOT / "app" / "static" / "samples"


def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY is not set.", file=sys.stderr)
        return 1

    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        samples = json.load(f)

    print(f"Running agent against {len(samples)} demo samples...\n")
    for sample in samples:
        image_path = SAMPLES_DIR / f"{sample['id']}.png"
        image_bytes = image_path.read_bytes()

        result = check_image(
            image_bytes,
            shop_name=sample["shop_name_to_enter"] or None,
            lang="en",
        )

        print(f"--- {sample['id']} ---")
        print(f"Expected: {sample['expected_verdict']}")
        print(f"Actual:   {result['verdict']} (score {result['score']})")
        print(f"Observed: {result['observed_shop_name']}")
        print("Tool calls:")
        for tc in result["tool_calls"]:
            print(f"  - {tc['name']}")
        print(f"Explanation: {result['explanation'][:200]}...")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
