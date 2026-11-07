"""Tables from a text-layer PDF (pdfplumber) or from OCR line boxes.

pdfplumber is used only on native text. It is not an OCR engine. OCR row
grouping keeps the engine's boxes and scores unchanged.
"""

from __future__ import annotations

from pathlib import Path

from processors.ocr_processor import table_rows_to_text


def tables_from_pdf(pdf: Path, page_number: int) -> str:
    try:
        import pdfplumber
    except Exception:
        return ""
    rows: list[str] = []
    with pdfplumber.open(pdf) as document:
        page = document.pages[page_number - 1]
        for table in page.extract_tables() or []:
            for row in table:
                cells = [str(cell).strip() for cell in row if cell and str(cell).strip()]
                if cells:
                    rows.append(" | ".join(cells))
    return "\n".join(rows).strip()


def words_from_pdf(pdf: Path, page_number: int) -> str:
    """Native word stream. Used to prove the text layer is readable without OCR."""
    import pdfplumber

    with pdfplumber.open(pdf) as document:
        words = document.pages[page_number - 1].extract_words() or []
    return " ".join(word.get("text", "") for word in words).strip()


def tables_from_ocr_lines(lines: list[dict]) -> str:
    usable = [line for line in lines if (line.get("box") or line.get("poly")) and line.get("text")]
    if not usable:
        return ""
    return table_rows_to_text(usable)
