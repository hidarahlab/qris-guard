# QRIS Guard: MVP Spec (for the coding agent)

Hackathon: Google Cloud AI Builder Cup 2026, Theme: BFSI. All user-facing text and code comments in **English** (UI has an EN/ID toggle, default EN). Hard deadline: Oct 18, 2026. Build for a flawless 3-minute demo, not for breadth.

## 1. One-sentence MVP
User uploads a photo of a QRIS code (optionally types the shop name they see), and instantly gets a verdict **Safe (no red flags detected) / Caution / Do not pay** with reasons, in English or Indonesian.

## 2. Non-goals (do NOT build)
Login, history, real database, real merchant lookups, payments, full MCP server. The fraud-pattern catalog is a local JSON file; the pitch calls it "the seed of an MCP server".

## 3. Architecture
Stack: Python 3.12, FastAPI, single static HTML page, Gemini via the official `google-genai` SDK, deployed on Cloud Run (region asia-southeast2). Model name comes from env `GEMINI_MODEL` (never hardcode; default to a current Gemini Flash model, verify the name in AI Studio).

Flow:
1. `POST /api/check` (multipart: `image`, optional `shop_name`, `lang`).
2. `decode_qr(image)`: plain code (OpenCV `QRCodeDetector` first, `pyzbar` as fallback) returns the payload string, or an error.
3. Gemini vision reads the **shop name visible in the photo** (signage, standee, sticker). If the user typed `shop_name`, that overrides it. Output: `observed_shop_name` or null.
4. Gemini agent (function calling) calls the tools below. If Gemini skips a tool, the orchestrator calls the missing tool itself so the pipeline is always complete.
5. **Verdict is computed in code from the risk score. Gemini must never change it.** Gemini only writes the explanation.
6. Response JSON includes the verdict, score, reasons, explanation, and the full ordered list of tool calls (name, args, result) for the UI panel.

## 4. Tools (deterministic code, no LLM inside)

### `parse_qris(payload: str) -> dict`
EMVCo TLV parser: 2-digit tag, 2-digit length, value. Return:
`merchant_name` (tag 59), `merchant_city` (60), `merchant_category` (52), `country` (58), `currency` (53), `amount` (54, optional), `type` ("static" if tag 01 == "11", "dynamic" if "12"), `qris_domain_found` (bool: tags 26-51 contain a sub-tag with a QRIS identifier such as "ID.CO.QRIS.WWW", case-insensitive), `crc_valid` (bool), `raw_fields`, `errors` (list).
CRC: tag 63, 4 hex chars at the end. CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF) computed over the payload up to and including "6304". **Verify TLV structure and CRC against 2-3 real QRIS samples the user provides before trusting the tests.** Never crash on malformed input; return `errors`.

### `check_name_match(qr_name: str | None, observed_name: str | None) -> dict`
Normalize (lowercase, strip punctuation, drop legal prefixes such as "PT", "CV", "TOKO", "WARUNG"), then `difflib.SequenceMatcher` ratio plus token overlap. Return `{"match": "exact|partial|mismatch|unknown", "similarity": float}`. `unknown` if either name is missing.

### `risk_score(parsed: dict, name_match: dict) -> dict`
Rules and weights live in `app/config/scoring.json` (easy to tune). Initial values:

| Signal | Points |
|---|---|
| name_match == mismatch | +60 |
| name_match == partial | +25 |
| crc_valid == false | +25 |
| merchant_name empty/missing | +20 |
| qris_domain_found == false | +30 |
| matches a pattern in `fraud_patterns.json` | +70 |

Thresholds: 0-24 SAFE (label "No red flags detected", never "100% safe"), 25-54 CAUTION, 55+ DO_NOT_PAY. Return `{"score": int, "verdict": str, "reasons": [str], "matched_patterns": [id]}`. Reasons are short, plain-language, bilingual (`{"en": "...", "id": "..."}`).

### Data: `app/data/fraud_patterns.json`
8-10 synthetic patterns, each `{id, title, description, signals, advice}`. Examples: sticker overlay on a legitimate QR (name mismatch), personal-name merchant at a business location, static QR with a suspicious amount, copy-pasted QR with broken CRC, generic names like "TOKO ONLINE 123". Clearly labeled synthetic.

## 5. Gemini usage (this is what the judges score at 40%)
- Vision: read the shop name from the photo (structured output, JSON).
- Function calling: choose and call the 3 tools; show them in the UI.
- Explanation: write 2-4 sentences for a non-technical person, in the requested language, grounded **only** in tool outputs. System prompt must forbid inventing facts and forbid changing the verdict.
- Handle API failure gracefully: if Gemini fails, still return the deterministic verdict with template-based reasons and a note.

## 6. API
`POST /api/check` returns:
```json
{
  "verdict": "DO_NOT_PAY",
  "score": 60,
  "reasons": [{"en": "...", "id": "..."}],
  "explanation": "...",
  "observed_shop_name": "...",
  "parsed": {},
  "tool_calls": [{"name": "parse_qris", "args": {}, "result": {}}],
  "synthetic_data_notice": true
}
```
`GET /health` returns `{"status": "ok"}`. `GET /api/samples` lists demo scenarios (for the "try a sample" buttons).

## 7. UI (one page, clean, mobile-friendly)
Upload area (drag/drop + camera on mobile), optional "Shop name you see" input, EN/ID toggle, big color-coded verdict card (green/amber/red), explanation text, reasons list, and a collapsible-by-default-open **"Tools the agent called"** panel showing each tool call with its result. A visible footer label: "Demo data is synthetic." Sample buttons that load the demo QR images so the demo never depends on a live camera.

## 8. Demo scenarios (synthetic QR images generated with the `qrcode` library from crafted payloads)
1. Valid QR, name matches the shop name -> SAFE.
2. Valid QR, merchant name differs from the shop name (sticker-overlay scam) -> DO_NOT_PAY.
3. Corrupted CRC + empty merchant name -> CAUTION (score 45).
4. Photo of a standee/stand with the shop name visible (no typed name) -> Gemini reads the name -> verdict follows the match result.
Also ship `scripts/make_samples.py` to regenerate the QR images and fixtures.

## 9. Tests (pytest, must pass before deploy)
TLV parser on valid/malformed payloads, CRC valid/invalid, name matching (exact/partial/mismatch/unknown), score thresholds for all 4 scenarios, `/api/check` with Gemini mocked.

## 10. Build order (commit after each step)
1. `decode_qr` + `parse_qris` + CRC + tests.
2. `check_name_match` + `risk_score` + fraud_patterns.json + tests.
3. Sample generator + fixtures.
4. Gemini agent (vision + function calling + explanation + fallback).
5. `/api/check` + static UI.
6. Deploy to Cloud Run, test all 4 scenarios on the live URL.

## 11. Config and deploy
Env vars: `GEMINI_API_KEY` (use Secret Manager or `--set-env-vars` for the hackathon; never commit it), `GEMINI_MODEL`. Add to requirements as needed: `google-genai`, `python-multipart`, `opencv-python-headless`, `pyzbar` (needs `libzbar0` in the Dockerfile), `pillow`, `qrcode`, `pytest`. Keep the image small and the cold start fast.

## 12. Definition of done
Public Cloud Run URL works; all 4 scenarios give the expected verdicts live; the tool-calls panel shows real calls; the README documents setup; no secrets in git.
