"""Gemini connectivity check.

Usage:  python scripts/check_gemini.py [model]
Reads GEMINI_API_KEY from the environment and never prints it.
Runs one raw REST call and one SDK call, each with a hard timeout,
so a slow or hanging call shows up as a clear error instead of silence.
"""

import os
import sys
import time
from importlib.metadata import version

MODEL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
KEY = os.environ.get("GEMINI_API_KEY")
TIMEOUT_S = 40


def check_rest() -> None:
    import httpx

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    started = time.time()
    try:
        resp = httpx.post(
            url,
            headers={"x-goog-api-key": KEY},
            json={"contents": [{"parts": [{"text": "Say OK"}]}]},
            timeout=TIMEOUT_S,
        )
        print(f"[REST] HTTP {resp.status_code} in {time.time() - started:.1f}s")
        print(resp.text[:500])
    except Exception as exc:  # noqa: BLE001
        print(f"[REST] failed after {time.time() - started:.1f}s: {type(exc).__name__}: {exc}")


def check_sdk() -> None:
    from google import genai
    from google.genai import types

    print(f"[SDK] google-genai {version('google-genai')}")
    started = time.time()
    try:
        client = genai.Client(
            api_key=KEY,
            http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000),
        )
        out = client.models.generate_content(model=MODEL, contents="Say OK")
        print(f"[SDK] ok in {time.time() - started:.1f}s -> {out.text!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"[SDK] failed after {time.time() - started:.1f}s: {type(exc).__name__}: {exc}")


def main() -> int:
    if not KEY:
        print("GEMINI_API_KEY is not set in this shell. Run: export GEMINI_API_KEY=...")
        return 1
    print(f"model: {MODEL}  key prefix: {KEY[:3]}  key length: {len(KEY)}")
    check_rest()
    check_sdk()
    return 0


if __name__ == "__main__":
    sys.exit(main())
