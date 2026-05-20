"""
OCR fallback for scanned PTR PDFs (paper filings, DocID starting with 8...).

These have no text layer, so we render each page to an image with poppler
(pdf2image) and run Tesseract. OCR output is noisier than a real text layer,
so every transaction parsed from it is tagged low confidence.

The tuning knobs below (DPI, Tesseract page-segmentation mode) are the main
levers for improving OCR accuracy on these forms.
"""
import logging

logger = logging.getLogger(__name__)

# ── Tuning knobs ────────────────────────────────────────────────────────────
OCR_DPI = 300                 # higher = sharper glyphs, slower
TESSERACT_CONFIG = "--psm 6"  # psm 6 = assume a uniform block of text (keeps rows)
OCR_LANG = "eng"


def ocr_pdf(pdf_path: str) -> str:
    """Render a scanned PDF to images and OCR them. Returns concatenated text."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        logger.error(f"OCR dependencies unavailable: {exc}")
        return ""

    try:
        images = convert_from_path(pdf_path, dpi=OCR_DPI)
    except Exception as exc:
        logger.warning(f"pdf2image failed for {pdf_path}: {exc}")
        return ""

    pages: list[str] = []
    for page_num, image in enumerate(images, start=1):
        try:
            pages.append(
                pytesseract.image_to_string(
                    image, lang=OCR_LANG, config=TESSERACT_CONFIG
                )
            )
        except Exception as exc:
            logger.warning(f"Tesseract failed on page {page_num}: {exc}")

    return "\n".join(pages)
