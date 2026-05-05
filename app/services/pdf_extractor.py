import pdfplumber
import fitz   # PyMuPDF
from pathlib import Path
from app.config import settings

def extract_text_from_pdf(pdf_path: str) -> dict:
    """
    Extract text from a PDF file.
    Primary: pdfplumber (digital PDFs)
    Fallback: PyMuPDF text extraction
    Returns: {full_text, pages, page_count, method}
    """
    path = Path(pdf_path)
    pages_data = []
    full_text  = ""
    method     = "pdfplumber"

    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                pages_data.append({"page": i, "text": text})
                full_text += f"\n--- Page {i} ---\n{text}"

        # Check if extraction was good
        avg_chars = len(full_text) / max(page_count, 1)
        if avg_chars < 50:
            # Fallback to PyMuPDF
            full_text, pages_data, method = _pymupdf_extract(path)
            page_count = len(pages_data)

    except Exception:
        full_text, pages_data, method = _pymupdf_extract(path)
        page_count = len(pages_data)

    return {
        "full_text":  full_text.strip(),
        "pages":      pages_data,
        "page_count": page_count,
        "method":     method,
    }


def _pymupdf_extract(path: Path) -> tuple:
    """PyMuPDF fallback text extraction."""
    doc = fitz.open(str(path))
    pages_data = []
    full_text  = ""
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        pages_data.append({"page": i, "text": text})
        full_text += f"\n--- Page {i} ---\n{text}"
    doc.close()
    return full_text.strip(), pages_data, "pymupdf"
