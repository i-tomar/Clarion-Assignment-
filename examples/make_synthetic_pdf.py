#!/usr/bin/env python3
"""Write a small text PDF with PyMuPDF. Synthetic strings only."""

from __future__ import annotations

import sys
from pathlib import Path


TEMPLATE = """EXAMPLE SCHEMA ONLY
Assignment Duration
Do not share any source code
Medical Document Processing
Amount 62042
Room total: 66666
"""

RECORD = """Patient Name: Asha Rao
Hospital Name: North Clinic
Doctor Name: Dr Meera Iyer
Date of Birth: 15/03/1980
City: Pune
Date of admission: 01/02/2024
Date of discharge: 03/02/2024
Date of Visit: 05/02/2024
Date: 09/02/2024
Symptoms: Thirst
Treatment taken: Oral fluids
Diagnosis: Mild dehydration
TPA ID: TPA-100
Account holder: Asha Rao
Bank name: Example Bank
Account number: 555544443333
IFSC: EXAM0001234
Bill group: Consult
Amount: 20000
Bill group: Labs
Amount: 10400
Subtotal: 30900
Total Medical Bills: 30900
Room type: General
Room category: Ward
Room total: 39000
Rate: 4000
Total days: 10
Discount: 1000
Total room amount: 39000
Tab. Saline
"""

OTHER = """Prescription
Patient Name: Rohan Das
Medicine: Tab. Mintol
This synthetic page is a typed prescription for a different person.
It is long enough to stay on the text layer in tests.
No scan is required for these repeated words.
""" + ("Synthetic typed line. " * 12)


def write_pdf(path: Path, pages: list[str]) -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        y = 72
        for line in text.splitlines():
            page.insert_text((72, y), line[:140], fontsize=11)
            y += 16
    document.save(path)
    document.close()


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/synthetic_record.pdf")
    write_pdf(dest, [TEMPLATE, RECORD, OTHER])
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
