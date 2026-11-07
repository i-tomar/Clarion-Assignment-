"""Label a page from its text layer. This is a heuristic, not a diagnosis."""

from __future__ import annotations

import re

# Phrases that mark the assignment's instruction/example pages.
# They are not patient facts. Matching them lets the pipeline keep the
# sample amounts 62042 and 66666 out of a real record.
TEMPLATE_MARKERS = (
    "example schema only",
    "assignment duration",
    "do not share any source code",
    "medical document processing",
)


def letter_count(text: str) -> int:
    return len(re.sub(r"[^A-Za-z]", "", text or ""))


def is_template_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in TEMPLATE_MARKERS)


def classify(text: str, *, force_ocr: bool = False, minimum_letters: int = 200) -> dict:
    letters = letter_count(text)
    template = is_template_text(text)
    if template:
        return {
            "template": True,
            "needs_ocr": False,
            "role": "instruction_template",
            "kind": "text",
            "letters": letters,
        }
    needs_ocr = bool(force_ocr or letters < minimum_letters)
    lowered = (text or "").lower()
    if needs_ocr:
        role = "scan"
        kind = "scan"
    elif any(token in lowered for token in ("subtotal", "bill group", "room total", "total medical bills")):
        role = "table"
        kind = "text"
    else:
        role = "text"
        kind = "text"
    return {
        "template": False,
        "needs_ocr": needs_ocr,
        "role": role,
        "kind": kind,
        "letters": letters,
    }
