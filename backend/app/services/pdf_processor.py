"""Turn an uploaded PDF or image into text (digital or OCR) plus page images on demand."""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass, field

import pymupdf
from PIL import Image, ImageOps

from backend.app.config import Settings
from backend.app.services.file_validation import DocumentError, FileInfo
from backend.app.services.ocr import rotate_clockwise, run_ocr, tesseract_available

logger = logging.getLogger(__name__)
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)


@dataclass
class PageContent:
    index: int
    text: str = ""
    # digital: embedded text layer; pdf_ocr_layer: text layer over a full-page scan;
    # ocr: Tesseract; none: nothing readable
    text_source: str = "none"
    ocr_confidence: float | None = None
    ocr_words: int = 0
    rotation: int = 0
    ocr_error: str | None = None
    ocr_attempted: bool = False
    vision_image: bytes | None = None  # JPEG, lazily filled


@dataclass
class DocumentContent:
    kind: str
    data: bytes = field(repr=False)
    pages_total: int
    pages: list[PageContent]
    truncated: bool
    settings: Settings = field(repr=False)
    _image: Image.Image | None = field(default=None, repr=False)

    # --- quality signals ----------------------------------------------------
    @property
    def digital_pages(self) -> int:
        return sum(p.text_source == "digital" for p in self.pages)

    @property
    def ocr_pages(self) -> int:
        return sum(p.ocr_attempted for p in self.pages)

    @property
    def scanned_layer_pages(self) -> int:
        return sum(p.text_source == "pdf_ocr_layer" for p in self.pages)

    @property
    def all_digital(self) -> bool:
        return bool(self.pages) and all(p.text_source == "digital" for p in self.pages)

    @property
    def ocr_used(self) -> bool:
        return any(p.ocr_attempted for p in self.pages)

    @property
    def ocr_unavailable(self) -> bool:
        """OCR was needed but could not run (Tesseract missing or OCR disabled) - a setup issue, not a bad scan."""
        attempted = [p for p in self.pages if p.ocr_attempted]
        return bool(attempted) and all(
            (p.ocr_error or "").startswith(("TESSERACT_UNAVAILABLE", "OCR_DISABLED")) for p in attempted
        )

    @property
    def ocr_error(self) -> str | None:
        return next((p.ocr_error for p in self.pages if p.ocr_attempted and p.ocr_error), None)

    @property
    def ocr_word_count(self) -> int:
        return sum(p.ocr_words for p in self.pages)

    @property
    def ocr_mean_confidence(self) -> float | None:
        """Word-weighted mean OCR confidence over OCR'd pages."""
        weighted = [(p.ocr_confidence, p.ocr_words) for p in self.pages if p.ocr_confidence is not None]
        words = sum(w for _, w in weighted)
        if not words:
            return None
        return round(sum(c * w for c, w in weighted) / words, 1)

    @property
    def has_text(self) -> bool:
        return any(p.text.strip() for p in self.pages)

    def text(self, max_chars: int) -> str:
        parts = []
        for page in self.pages:
            if page.text.strip():
                parts.append(f"--- page {page.index + 1} ---\n{page.text.strip()}")
        joined = "\n\n".join(parts)
        if len(joined) > max_chars:
            joined = joined[:max_chars] + "\n[... text truncated ...]"
        return joined

    # --- page images for the vision model ----------------------------------
    def vision_images(self, max_pages: int, max_side: int) -> list[bytes]:
        images: list[bytes] = []
        for page in self.pages[:max_pages]:
            if page.vision_image is None:
                page.vision_image = self._render_vision_image(page, max_side)
            if page.vision_image:
                images.append(page.vision_image)
        return images

    def _render_vision_image(self, page: PageContent, max_side: int) -> bytes:
        if self.kind == "image":
            img = self._image if self._image is not None else _open_image(self.data, self.settings)
        else:
            with pymupdf.open(stream=self.data, filetype="pdf") as doc:
                pdf_page = doc[page.index]
                rect = pdf_page.rect
                zoom = max_side / max(rect.width, rect.height, 1)
                zoom = max(0.5, min(zoom, 4.0))
                pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = rotate_clockwise(img, page.rotation)
        return _encode_jpeg(img, max_side)


def _encode_jpeg(img: Image.Image, max_side: int) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def _open_image(data: bytes, settings: Settings) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DocumentError("IMAGE_TOO_LARGE", "Image resolution exceeds the processing limit.")
    except Exception:
        raise DocumentError("CORRUPTED_FILE", "The image could not be decoded (corrupted or truncated).")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _meaningful_chars(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def _looks_like_garbage(text: str) -> bool:
    """Broken font encodings produce replacement chars / private-use glyphs."""
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return False
    bad = sum(1 for c in stripped if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or ord(c) < 32)
    readable = sum(1 for c in stripped if c.isalnum() or c in ".,:;-/#()%&@₹$€£+*'\"")
    return bad / len(stripped) > 0.1 or readable / len(stripped) < 0.6


def _full_page_image_coverage(page: "pymupdf.Page") -> float:
    area = abs(page.rect.width * page.rect.height) or 1.0
    best = 0.0
    try:
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            best = max(best, abs((x1 - x0) * (y1 - y0)) / area)
    except Exception:
        return 0.0
    return best


def _ocr_page(page: PageContent, img: Image.Image, settings: Settings) -> None:
    page.ocr_attempted = True
    if not settings.ocr_enabled:
        page.ocr_error = "OCR_DISABLED"
        return
    result = run_ocr(img, lang=settings.ocr_lang, timeout=settings.ocr_timeout_s)
    page.rotation = result.rotation
    page.ocr_error = result.error
    page.ocr_confidence = result.mean_confidence
    page.ocr_words = result.word_count
    if result.text.strip():
        page.text = result.text
        page.text_source = "ocr"


def load_document(data: bytes, info: FileInfo, settings: Settings) -> DocumentContent:
    if info.kind == "image":
        img = _open_image(data, settings)
        page = PageContent(index=0)
        _ocr_page(page, img, settings)
        return DocumentContent("image", data, 1, [page], False, settings, img)
    return _load_pdf(data, settings)


def _load_pdf(data: bytes, settings: Settings) -> DocumentContent:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise DocumentError("CORRUPTED_FILE", "The PDF could not be opened (corrupted or not a real PDF).")
    with doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise DocumentError("ENCRYPTED_PDF", "The PDF is password-protected.")
        try:
            total = doc.page_count
        except Exception:
            total = 0
        if total == 0:
            raise DocumentError("CORRUPTED_FILE", "The PDF contains no readable pages.")
        limit = min(total, settings.max_pdf_pages)
        pages: list[PageContent] = []
        for i in range(limit):
            page = PageContent(index=i)
            try:
                pdf_page = doc[i]
                text = pdf_page.get_text("text", sort=True) or ""
                coverage = _full_page_image_coverage(pdf_page)
            except Exception:
                raise DocumentError("CORRUPTED_FILE", f"Page {i + 1} of the PDF could not be read.")
            if _meaningful_chars(text) >= settings.min_text_chars_per_page and not _looks_like_garbage(text):
                page.text = text
                # A text layer sitting on top of a full-page picture is a previous OCR pass.
                page.text_source = "pdf_ocr_layer" if coverage >= 0.7 else "digital"
            else:
                try:
                    zoom = settings.ocr_dpi / 72
                    pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                except Exception:
                    raise DocumentError("CORRUPTED_FILE", f"Page {i + 1} could not be rendered.")
                _ocr_page(page, img, settings)
            pages.append(page)
    return DocumentContent("pdf", data, total, pages, total > limit, settings)


def ocr_available() -> bool:
    return tesseract_available()
