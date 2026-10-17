"""QR code decoding from images with robust preprocessing."""

from __future__ import annotations

import io
from typing import Callable, Iterator, Union

import cv2
import numpy as np
from PIL import Image


ImageInput = Union[bytes, Image.Image, np.ndarray]


def _to_grayscale(image: ImageInput) -> tuple[np.ndarray, str | None]:
    """Convert an image input to a grayscale numpy array.

    Returns (gray_array, error_message).
    """
    if isinstance(image, bytes):
        try:
            pil_image = Image.open(io.BytesIO(image))
        except Exception as exc:  # pragma: no cover - broad guard for corrupt bytes
            return np.array([]), f"Could not open image bytes: {exc}"
        arr = np.array(pil_image)
    elif isinstance(image, Image.Image):
        arr = np.array(image)
    elif isinstance(image, np.ndarray):
        arr = image
    else:
        return np.array([]), f"Unsupported image type: {type(image)}"

    if arr.size == 0:
        return np.array([]), "Empty image"

    if arr.dtype == bool:
        arr = arr.astype(np.uint8) * 255
    elif arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)

    if len(arr.shape) == 2:
        return arr, None

    if len(arr.shape) == 3:
        channels = arr.shape[2]
        if channels == 3:
            # PIL loads RGB; OpenCV wants BGR, so use the correct conversion.
            return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), None
        if channels == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY), None

    return np.array([]), f"Unsupported image shape: {arr.shape}"


def _pyzbar_decode(gray: np.ndarray):
    """Lazy import of pyzbar so the module loads when libzbar is missing."""
    from pyzbar.pyzbar import decode as _decode
    return _decode(gray)


def _try_opencv(gray: np.ndarray) -> str | None:
    """Attempt OpenCV QR detection on a grayscale image."""
    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(gray)
    return data if data else None


def _preprocessors(gray: np.ndarray) -> Iterator[tuple[str, np.ndarray]]:
    """Yield progressively aggressive preprocessing variants for QR detection."""
    # 1. Plain grayscale.
    yield "opencv_plain", gray

    # 2. Upscale 2x and 3x (helps small or low-resolution QRs).
    for scale in (2, 3):
        upscaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        yield f"opencv_upscaled_{scale}x", upscaled

    # 3. CLAHE contrast enhancement + adaptive threshold.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)
    threshold = cv2.adaptiveThreshold(
        contrast, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    yield "opencv_adaptive_threshold", threshold

    # Also apply CLAHE/threshold to upscaled variants.
    for scale in (2, 3):
        upscaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        contrast = clahe.apply(upscaled)
        threshold = cv2.adaptiveThreshold(
            contrast, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        yield f"opencv_adaptive_threshold_upscaled_{scale}x", threshold

    # 4. Pad a white border (helps when the QR is at the image edge or partially cropped).
    padded = cv2.copyMakeBorder(gray, 50, 50, 50, 50, cv2.BORDER_CONSTANT, value=(255,))
    yield "opencv_padded", padded


def decode_qr(image: ImageInput) -> dict:
    """Decode a QR code from an image using multiple robustness strategies.

    Strategy order:
      1. OpenCV QRCodeDetector on plain grayscale.
      2. OpenCV on upscaled 2x/3x variants.
      3. CLAHE + adaptive threshold on original and upscaled variants.
      4. White border padding.
      5. pyzbar fallback (skipped gracefully if libzbar is not installed).

    Returns {"payload": str, "strategy": str} on success or {"error": str} on failure.
    """
    gray, error = _to_grayscale(image)
    if error:
        return {"error": error}

    for strategy, candidate in _preprocessors(gray):
        data = _try_opencv(candidate)
        if data:
            return {"payload": data, "strategy": strategy}

    # Fallback: pyzbar (requires libzbar; optional on dev machines).
    try:
        decoded = _pyzbar_decode(gray)
    except ImportError:
        decoded = []
    if decoded:
        payload = decoded[0].data.decode("utf-8", errors="replace")
        return {"payload": payload, "strategy": "pyzbar"}

    return {"error": "No QR code found in image"}
