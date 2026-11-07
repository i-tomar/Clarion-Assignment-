"""Native PDF text and rendering.

Primary backend: PyMuPDF (imported as `fitz`), used for the text layer and
for page images. Poppler (`pdftotext` / `pdftoppm`) is a fallback when
PyMuPDF is not installed. This module does not OCR and does not call a network.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class PdfError(RuntimeError):
    pass


def backend_name() -> str:
    try:
        import fitz  # noqa: F401
    except Exception:
        return "poppler"
    return "pymupdf"


class Document:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PdfError(f"PDF not found: {self.path}")
        self.backend = backend_name()
        self._doc = None
        if self.backend == "pymupdf":
            import fitz

            self._doc = fitz.open(self.path)
            self.page_count = self._doc.page_count
        else:
            self.page_count = _poppler_page_count(self.path)

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def text(self, page_number: int) -> str:
        if page_number < 1 or page_number > self.page_count:
            raise PdfError(f"page {page_number} is outside 1..{self.page_count}")
        if self.backend == "pymupdf":
            raw = self._doc[page_number - 1].get_text("text") or ""
        else:
            raw = _poppler_text(self.path, page_number)
        return _clean_text(raw, page_number)

    def render(self, page_number: int, dest_dir: Path, dpi: int = 200) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"page-{page_number:02d}.png"
        if self.backend == "pymupdf":
            pixmap = self._doc[page_number - 1].get_pixmap(dpi=dpi)
            pixmap.save(target)
            return target
        prefix = dest_dir / f"render-{page_number:02d}"
        _run(["pdftoppm", "-r", str(dpi), "-f", str(page_number), "-l", str(page_number), "-png", str(self.path), str(prefix)])
        matches = sorted(dest_dir.glob(f"render-{page_number:02d}*.png"))
        if not matches:
            raise PdfError(f"pdftoppm produced no image for page {page_number}")
        matches[0].replace(target)
        return target


def _clean_text(raw: str, page_number: int) -> str:
    text = raw.replace("\u200b", "")
    lines = []
    for line in text.splitlines():
        if re.fullmatch(rf"\s*{page_number}\s*/\s*\d+\s*", line):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise PdfError(
            "Neither PyMuPDF nor Poppler is available "
            f"({command[0]}). Install PyMuPDF (`pip install PyMuPDF==1.26.7`) "
            "or Poppler (Linux: apt install poppler-utils)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise PdfError(f"{command[0]} failed: {detail or exc}") from exc


def _poppler_page_count(pdf: Path) -> int:
    result = _run(["pdfinfo", str(pdf)])
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout.decode("utf-8", errors="replace"), re.M)
    if not match:
        raise PdfError("pdfinfo did not report a page count")
    return int(match.group(1))


def _poppler_text(pdf: Path, page_number: int) -> str:
    result = _run(["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", str(pdf), "-"])
    return result.stdout.decode("utf-8", errors="replace")
