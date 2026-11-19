"""Tesseract OCR with light, conservative image preprocessing."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    text: str
    mean_confidence: float | None  # word-level mean (0-100); None when no words
    word_count: int
    rotation: int = 0  # degrees applied (clockwise) to make the page upright
    error: str | None = None


@lru_cache(maxsize=1)
def tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:  # pragma: no cover - depends on host
        return False


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Grayscale, upscale small images, normalise contrast, remove speckle noise."""
    img = ImageOps.exif_transpose(image)
    img = img.convert("L")
    width, height = img.size
    long_side = max(width, height)
    if long_side < 1800:  # tesseract likes ~30px tall characters
        scale = min(3.0, 2200 / long_side)
        img = img.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    return img


def detect_rotation(image: Image.Image, timeout: int) -> int:
    """Use Tesseract OSD to detect 90/180/270 rotation. Returns clockwise degrees to apply."""
    import pytesseract

    try:
        osd = pytesseract.image_to_osd(
            image, output_type=pytesseract.Output.DICT, config="--psm 0", timeout=timeout
        )
    except Exception:
        return 0  # OSD fails on sparse pages; that's fine
    rotate = int(osd.get("rotate", 0) or 0)
    confidence = float(osd.get("orientation_conf", 0) or 0)
    if rotate in (90, 180, 270) and confidence >= 2.0:
        return rotate
    return 0


def rotate_clockwise(image: Image.Image, degrees: int) -> Image.Image:
    if not degrees:
        return image
    return image.rotate(-degrees, expand=True)


def run_ocr(image: Image.Image, lang: str = "eng", timeout: int = 60) -> OCRResult:
    if not tesseract_available():
        return OCRResult("", None, 0, error="TESSERACT_UNAVAILABLE")
    import pytesseract

    try:
        prepared = preprocess_for_ocr(image)
        rotation = detect_rotation(prepared, timeout=min(timeout, 20))
        prepared = rotate_clockwise(prepared, rotation)
        data = pytesseract.image_to_data(
            prepared,
            lang=lang,
            config="--oem 1 --psm 3 -c preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
        logger.warning("OCR timed out")
        return OCRResult("", None, 0, error=f"OCR_TIMEOUT: {exc}")
    except Exception as exc:
        logger.warning("OCR failed: %s", type(exc).__name__)
        return OCRResult("", None, 0, error=f"OCR_FAILED: {type(exc).__name__}")

    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf >= 0:
            confidences.append(conf)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)

    text_lines: list[str] = []
    previous_block = None
    for (block, _par, _line), words in lines.items():
        if previous_block is not None and block != previous_block:
            text_lines.append("")
        text_lines.append(" ".join(words))
        previous_block = block

    mean_conf = round(sum(confidences) / len(confidences), 1) if confidences else None
    return OCRResult("\n".join(text_lines).strip(), mean_conf, len(confidences), rotation)
