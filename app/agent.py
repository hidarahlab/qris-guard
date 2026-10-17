"""Gemini agent: vision shop-name reading, function calling, and explanation."""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types
from PIL import Image

from app.name_match import check_name_match
from app.qr import decode_qr
from app.qris import parse_qris
from app.risk_score import risk_score


logger = logging.getLogger("qris_guard.agent")

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.6-flash"

_VISION_PROMPT = {
    "en": (
        "Look at this photo of a QRIS payment QR code. "
        "Read only the shop/merchant name visible in the photo (on signage, standee, sticker, or stand). "
        "Return JSON with a single key 'observed_shop_name'. Use null if no shop name is visible."
    ),
    "id": (
        "Lihat foto kode QRIS ini. "
        "Baca hanya nama toko/pedagang yang terlihat di foto (di papan nama, standee, stiker, atau meja). "
        "Kembalikan JSON dengan kunci 'observed_shop_name'. Gunakan null jika tidak ada nama toko."
    ),
}

_TOOL_CALLING_PROMPT = {
    "en": (
        "You are an orchestrator for a QRIS fraud-checking pipeline. "
        "Call the available tools to analyse this QRIS payload and the observed shop name. "
        "You must call all three tools if possible: parse_qris, check_name_match, and risk_score. "
        "Do not change the verdict or score; only the tools compute them."
    ),
    "id": (
        "Anda adalah orkestrator untuk pipeline pemeriksaan QRIS. "
        "Panggil tool yang tersedia untuk menganalisis payload QRIS dan nama toko yang terlihat. "
        "Panggil ketiga tool jika memungkinkan: parse_qris, check_name_match, dan risk_score. "
        "Jangan ubah verdict atau skor; hanya tool yang menghitungnya."
    ),
}

_EXPLANATION_SYSTEM_PROMPT = {
    "en": (
        "You are a helpful assistant explaining a QRIS fraud-check result. "
        "Write 2-4 short sentences for a non-technical person. "
        "Ground the explanation ONLY in the provided tool outputs. "
        "Do not invent facts. Do not change the verdict or score. "
        "Answer in English."
    ),
    "id": (
        "Anda adalah asisten yang menjelaskan hasil pemeriksaan QRIS. "
        "Tulis 2-4 kalimat pendek untuk orang awam. "
        "Dasarkan penjelasan HANYA pada output tool yang diberikan. "
        "Jangan mengada-ada fakta. Jangan ubah verdict atau skor. "
        "Jawab dalam Bahasa Indonesia."
    ),
}

_UNVERIFIED_SHOP_REASON = {
    "en": "Shop name was not verified; enter the name you see for a stronger check.",
    "id": "Nama toko belum diverifikasi; masukkan nama yang Anda lihat untuk pemeriksaan lebih kuat.",
}

_API_FAILURE_NOTE = {
    "en": "The AI explanation service is temporarily unavailable; the verdict below is based on deterministic checks only.",
    "id": "Layanan penjelasan AI sedang tidak tersedia; verdict di bawah hanya berdasarkan pemeriksaan deterministik.",
}


def _get_model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")
    return genai.Client(api_key=api_key)


def _low_thinking_config() -> types.ThinkingConfig:
    return types.ThinkingConfig(thinking_budget=0)


def _http_options(timeout: int = 35) -> types.HttpOptions:
    return types.HttpOptions(timeout=timeout * 1000)  # SDK expects milliseconds


def _read_observed_shop_name(image: Image.Image, lang: str) -> str | None:
    """Use Gemini vision to read the shop name visible in the photo."""
    client = _get_client()
    model = _get_model_name()

    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "observed_shop_name": types.Schema(
                type=types.Type.STRING,
                nullable=True,
                description="Shop or merchant name visible in the photo, or null if none",
            )
        },
        required=["observed_shop_name"],
    )

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=_low_thinking_config(),
        http_options=_http_options(30),
    )

    response = client.models.generate_content(
        model=model,
        contents=[image, _VISION_PROMPT.get(lang, _VISION_PROMPT["en"])],
        config=config,
    )

    text = response.text or "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    name = data.get("observed_shop_name")
    if name is None or str(name).strip().lower() in ("null", "none", ""):
        return None
    return str(name).strip()


def _build_tool_declarations() -> list[types.Tool]:
    parse_qris_schema = types.FunctionDeclaration(
        name="parse_qris",
        description="Parse an EMVCo QRIS payload and validate its CRC.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "payload": types.Schema(type=types.Type.STRING, description="The raw QRIS payload string from the QR code")
            },
            required=["payload"],
        ),
    )

    check_name_match_schema = types.FunctionDeclaration(
        name="check_name_match",
        description="Compare the merchant name in the QR with the observed shop name.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "qr_name": types.Schema(type=types.Type.STRING, nullable=True, description="Merchant name from the QR code"),
                "observed_name": types.Schema(
                    type=types.Type.STRING, nullable=True, description="Shop name observed by the user or from vision"
                ),
            },
            required=["qr_name", "observed_name"],
        ),
    )

    risk_score_schema = types.FunctionDeclaration(
        name="risk_score",
        description="Compute the risk score and verdict from parsed QRIS data and name match result.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "parsed": types.Schema(type=types.Type.OBJECT, description="Output from parse_qris"),
                "name_match": types.Schema(type=types.Type.OBJECT, description="Output from check_name_match"),
            },
            required=["parsed", "name_match"],
        ),
    )

    return [types.Tool(function_declarations=[parse_qris_schema, check_name_match_schema, risk_score_schema])]


def _execute_tool_call(call: types.FunctionCall) -> dict:
    name = call.name
    args = call.args or {}

    if name == "parse_qris":
        return parse_qris(args["payload"])
    if name == "check_name_match":
        return check_name_match(args.get("qr_name"), args.get("observed_name"))
    if name == "risk_score":
        parsed = args.get("parsed", {})
        name_match = args.get("name_match", {})
        return risk_score(parsed, name_match)

    raise ValueError(f"Unknown tool call: {name}")


def _run_tool_orchestrator(
    payload: str, observed_shop_name: str | None
) -> tuple[dict, dict, dict, list[dict]]:
    """Run the three tools, using Gemini function calling with deterministic fallback."""
    client = _get_client()
    model = _get_model_name()

    # Always compute deterministic baseline first.
    parsed = parse_qris(payload)
    name_match = check_name_match(parsed.get("merchant_name"), observed_shop_name)

    tool_outputs: dict[str, dict] = {}
    tool_calls: list[dict] = []

    prompt = (
        f"QRIS payload: {payload}\n"
        f"Observed shop name: {observed_shop_name or 'null'}\n"
        "Call parse_qris, check_name_match, and risk_score to complete the analysis."
    )

    config = types.GenerateContentConfig(
        tools=_build_tool_declarations(),
        temperature=0,
        max_output_tokens=512,
        thinking_config=None,
        http_options=_http_options(35),
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

        for call in response.function_calls or []:
            call_name = call.name or ""
            if not call_name or call_name in tool_outputs:
                continue
            try:
                result = _execute_tool_call(call)
            except Exception as exc:
                result = {"error": str(exc)}
            tool_outputs[call_name] = result
            tool_calls.append({"name": call_name, "args": call.args, "result": result})
    except Exception as exc:
        # On Gemini failure, fall through to deterministic tools.
        logger.warning("Gemini tool calling failed: %s: %s", type(exc).__name__, exc)

    # Ensure all three tools appear in tool_calls, and compute the final state
    # deterministically so the verdict can never be altered by the LLM.
    required_tools = ["parse_qris", "check_name_match", "risk_score"]
    for tool_name in required_tools:
        if tool_name not in tool_outputs:
            if tool_name == "parse_qris":
                result = parsed
            elif tool_name == "check_name_match":
                result = name_match
            else:
                result = risk_score(parsed, name_match)
            tool_outputs[tool_name] = result
            tool_calls.append({"name": tool_name, "args": {}, "result": result})

    final_parsed = parsed
    final_name_match = name_match
    final_score = risk_score(final_parsed, final_name_match)

    return final_parsed, final_name_match, final_score, tool_calls


_VERDICT_TEMPLATES = {
    "SAFE": {
        "en": "The QR code decoded successfully, the merchant name matches the shop you see, and the QRIS identifier and checksum are valid. No red flags were found.",
        "id": "Kode QR berhasil dibaca, nama pedagang cocok dengan toko yang Anda lihat, dan identitas QRIS serta checksum valid. Tidak ada tanda bahaya.",
    },
    "CAUTION": {
        "en": "The QR code is readable, but one or more warnings were found (e.g. partial name match, invalid checksum, or missing QRIS identifier). Please double-check before paying.",
        "id": "Kode QR dapat dibaca, tetapi ditemukan satu atau lebih peringatan (misalnya nama cocok sebagian, checksum tidak valid, atau identitas QRIS hilang). Periksa lagi sebelum membayar.",
    },
    "DO_NOT_PAY": {
        "en": "This QR code shows clear warning signs, such as a merchant name that does not match the shop or a broken checksum. Do not complete the payment.",
        "id": "Kode QR ini menunjukkan tanda bahaya yang jelas, seperti nama pedagang yang tidak cocok dengan toko atau checksum rusak. Jangan lanjutkan pembayaran.",
    },
}


def _fallback_explanation(verdict: str, reasons: list, lang: str) -> str:
    template = _VERDICT_TEMPLATES.get(verdict, _VERDICT_TEMPLATES["SAFE"])
    text = template.get(lang, template["en"])
    detail_reasons = [r.get(lang, r.get("en", "")) for r in reasons if r]
    if detail_reasons:
        text += " " + " ".join(detail_reasons)
    return text


def _generate_explanation(
    tool_outputs: dict, verdict: str, score: int, reasons: list, lang: str
) -> str:
    """Ask Gemini to write a grounded explanation."""
    client = _get_client()
    model = _get_model_name()

    summary = {
        "verdict": verdict,
        "score": score,
        "merchant_name": tool_outputs.get("parse_qris", {}).get("merchant_name"),
        "crc_valid": tool_outputs.get("parse_qris", {}).get("crc_valid"),
        "name_match": tool_outputs.get("check_name_match", {}).get("match"),
        "reasons": [r.get(lang, r.get("en", "")) for r in reasons if r],
    }

    system_prompt = _EXPLANATION_SYSTEM_PROMPT.get(lang, _EXPLANATION_SYSTEM_PROMPT["en"])
    user_prompt = (
        "Based ONLY on the following summary, write a short explanation "
        "(2-4 sentences) for the user.\n\n" + json.dumps(summary, ensure_ascii=False, indent=2)
    )

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=256,
        thinking_config=_low_thinking_config(),
        http_options=_http_options(35),
    )

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=config,
    )

    text = (response.text or "").strip()
    if not text:
        return _fallback_explanation(verdict, reasons, lang)
    return text


def check_image(
    image_bytes: bytes,
    shop_name: str | None = None,
    lang: str = "en",
) -> dict:
    """Run the full QRIS Guard pipeline on an uploaded image.

    Returns a result dict with verdict, score, reasons, explanation, observed_shop_name,
    parsed QRIS data, and the ordered list of tool calls.
    """
    # 1. Decode QR.
    decoded = decode_qr(image_bytes)
    if "error" in decoded:
        return {
            "verdict": "DO_NOT_PAY",
            "score": 100,
            "reasons": [{"en": "No QR code could be decoded.", "id": "Kode QR tidak dapat dibaca."}],
            "explanation": "",
            "observed_shop_name": None,
            "parsed": {},
            "tool_calls": [],
        }

    payload = decoded["payload"]

    # 2. Get observed shop name (typed overrides vision).
    observed_shop_name: str | None = None
    if shop_name:
        observed_shop_name = shop_name.strip() or None
    else:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            observed_shop_name = _read_observed_shop_name(image, lang)
        except Exception as exc:
            logger.warning("Gemini vision failed: %s: %s", type(exc).__name__, exc)
            observed_shop_name = None

    # 3. Run tools (with Gemini function calling + deterministic fallback).
    parsed, name_match, scored, tool_calls = _run_tool_orchestrator(payload, observed_shop_name)

    reasons = list(scored.get("reasons", []))

    # 4. Add unverified-shop reason if no name was available.
    if not observed_shop_name:
        reasons.append(_UNVERIFIED_SHOP_REASON)

    # 5. Generate explanation, with deterministic fallback on failure.
    try:
        tool_outputs_for_explanation = {
            "parse_qris": parsed,
            "check_name_match": name_match,
            "risk_score": scored,
        }
        explanation = _generate_explanation(
            tool_outputs_for_explanation,
            scored.get("verdict", "SAFE"),
            scored.get("score", 0),
            reasons,
            lang,
        )
    except Exception as exc:
        logger.warning("Gemini explanation failed: %s: %s", type(exc).__name__, exc)
        explanation = _fallback_explanation(
            scored.get("verdict", "SAFE"),
            reasons,
            lang,
        )
        reasons.append(_API_FAILURE_NOTE)

    return {
        "verdict": scored.get("verdict"),
        "score": scored.get("score"),
        "reasons": reasons,
        "explanation": explanation,
        "observed_shop_name": observed_shop_name,
        "parsed": parsed,
        "tool_calls": tool_calls,
    }
