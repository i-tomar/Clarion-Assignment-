"""Optional OCR. PaddleOCR is imported only when a scan actually needs it.

Engine scores stored with lines are the OCR engine's own numbers. They are
not extraction confidence and the pipeline does not turn them into one.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from statistics import median

PREPROCESS_REVISION = 3
OCR_CACHE_VERSION = 3


def ocr_cache_usable(stored: dict, *, pdf_sha256: str, page: int, backend: str, dpi: int) -> bool:
    return bool(
        isinstance(stored, dict)
        and stored.get("cache_version") == OCR_CACHE_VERSION
        and stored.get("pdf_sha256") == pdf_sha256
        and stored.get("page") == page
        and stored.get("backend") == backend
        and stored.get("dpi") == dpi
        and stored.get("preprocess_revision") == PREPROCESS_REVISION
        and not stored.get("error")
        and "text" in stored
    )


def rows_to_text(lines: list[dict], y_tolerance: float = 12) -> str:
    ordered = sorted(lines, key=lambda line: (line["box"][1], line["box"][0]))
    rows: list[dict] = []
    for line in ordered:
        box = line["box"]
        center = (box[1] + box[3]) / 2
        target = next((row for row in rows if abs(row["y"] - center) < y_tolerance), None)
        if target is None:
            rows.append({"y": center, "lines": [line]})
        else:
            target["lines"].append(line)
    rendered = []
    for row in rows:
        texts = [item["text"] for item in sorted(row["lines"], key=lambda item: item["box"][0])]
        rendered.append(" | ".join(texts))
    return "\n".join(rendered)


def table_rows_to_text(lines: list[dict]) -> str:
    """Rebuild logical table rows from OCR lines, or return "" when no table is seen.

    Screenshots of tables are often rotated a degree or two, and cells wrap
    onto several lines. Rows are aligned on each line's local rotation (from
    the OCR polygon), wrapped lines are stacked into their cell, and adjacent
    digit-free header rows are merged. Text is only regrouped, never edited.
    """
    items = [_geometry(line) for line in lines if line.get("text") and (line.get("poly") or line.get("box"))]
    items = [item for item in items if item is not None]
    if len(items) < 6:
        return ""
    unit = median(item["h"] for item in items) or 1.0
    for item in items:
        theta = math.atan(_local_slope(items, item, unit))
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        rotated = [(x * cos_t + y * sin_t, -x * sin_t + y * cos_t) for x, y in item["poly"]]
        item["top"] = rotated[0][1]
        item["x1"] = min(point[0] for point in rotated)
        item["x2"] = max(point[0] for point in rotated)

    bands: list[list[dict]] = []
    for item in sorted(items, key=lambda entry: entry["top"]):
        if bands and item["top"] - bands[-1][0]["top"] <= 0.5 * unit:
            bands[-1].append(item)
        else:
            bands.append([item])

    rows: list[list[list[dict]]] = []
    for band in bands:
        band.sort(key=lambda entry: entry["x1"])
        stacked = [(item, _stack_target(rows[-1], item, unit)) for item in band] if rows else [(item, None) for item in band]
        attached = [pair for pair in stacked if pair[1] is not None]
        if len(band) >= 3 and len(attached) * 2 < len(band):
            rows.append([[item] for item in band])
            continue
        for item, cell in attached:
            cell.append(item)
        loose = [item for item, cell in stacked if cell is None]
        if loose:
            rows.append([[item] for item in loose])

    merged: list[list[list[dict]]] = []
    for row in rows:
        if merged and _digit_free(merged[-1]) and _digit_free(row):
            last_top = max(item["top"] for cell in merged[-1] for item in cell)
            first_top = min(item["top"] for cell in row for item in cell)
            if first_top - last_top <= 1.9 * unit:
                merged[-1] = _cells([item for cell in merged[-1] + row for item in cell])
                continue
        merged.append(row)

    if sum(1 for row in merged if len(row) >= 3) < 2:
        return ""
    rendered = []
    for row in merged:
        cells = sorted(row, key=lambda cell: min(item["x1"] for item in cell))
        texts = [" ".join(item["text"] for item in sorted(cell, key=lambda entry: entry["top"])) for cell in cells]
        rendered.append(" | ".join(texts))
    return "\n".join(rendered)


def _geometry(line: dict) -> dict | None:
    poly = line.get("poly")
    if not poly or len(poly) != 4:
        box = line.get("box") or []
        if len(box) < 4:
            return None
        x1, y1, x2, y2 = (float(value) for value in box[:4])
        poly = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    points = [(float(point[0]), float(point[1])) for point in poly]
    (tlx, tly), (trx, try_), _br, (blx, bly) = points
    width = math.hypot(trx - tlx, try_ - tly)
    height = math.hypot(blx - tlx, bly - tly)
    if height <= 0:
        return None
    angle = math.atan2(try_ - tly, trx - tlx) if width >= 3 * height else None
    return {
        "text": str(line["text"]).strip(),
        "poly": points,
        "h": height,
        "angle": angle,
        "cy": sum(point[1] for point in points) / 4,
    }


def _local_slope(items: list[dict], item: dict, unit: float) -> float:
    """Most common top-edge slope between separated lines near `item`.

    Lines in one table row share that slope, so it is the rotation of the
    region. Single-word polygon angles are too noisy for this.
    """
    near = [other for other in items if abs(other["cy"] - item["cy"]) <= 6 * unit]
    slopes = []
    for index, left in enumerate(near):
        for right in near[index + 1:]:
            dx = right["poly"][0][0] - left["poly"][0][0]
            if abs(dx) < 4 * unit:
                continue
            slope = (right["poly"][0][1] - left["poly"][0][1]) / dx
            if abs(slope) <= 0.1:
                slopes.append(slope)
    if not slopes:
        return 0.0
    slopes.sort()
    width = 0.006
    best_start, best_count, start = 0, 0, 0
    for end in range(len(slopes)):
        while slopes[end] - slopes[start] > width:
            start += 1
        if end - start + 1 > best_count:
            best_start, best_count = start, end - start + 1
    return median(slopes[best_start:best_start + best_count])


def _stack_target(row: list[list[dict]], item: dict, unit: float) -> list[dict] | None:
    for cell in row:
        last = max(cell, key=lambda entry: entry["top"])
        left = min(entry["x1"] for entry in cell)
        right = max(entry["x2"] for entry in cell)
        overlap = min(right, item["x2"]) - max(left, item["x1"])
        narrow = min(right - left, item["x2"] - item["x1"])
        pitch = item["top"] - last["top"]
        if narrow > 0 and overlap >= 0.3 * narrow and 0.5 * unit < pitch <= 1.9 * unit:
            return cell
    return None


def _cells(items: list[dict]) -> list[list[dict]]:
    cells: list[list[dict]] = []
    for item in sorted(items, key=lambda entry: entry["top"]):
        for cell in cells:
            left = min(entry["x1"] for entry in cell)
            right = max(entry["x2"] for entry in cell)
            overlap = min(right, item["x2"]) - max(left, item["x1"])
            narrow = min(right - left, item["x2"] - item["x1"])
            if narrow > 0 and overlap >= 0.3 * narrow:
                cell.append(item)
                break
        else:
            cells.append([item])
    return cells


def _digit_free(row: list[list[dict]]) -> bool:
    return not any(re.search(r"\d", item["text"]) for cell in row for item in cell)


def prepare_image(image_path: Path, dest: Path) -> dict:
    """Invert a dark panel when Pillow and numpy are available. Never required."""
    note = {"inverted": False, "preprocess": "none"}
    try:
        from PIL import Image, ImageOps, ImageStat
        import numpy as np
    except Exception as exc:  # ImportError or a broken install
        note["preprocess"] = f"skipped: {type(exc).__name__}"
        return note
    image = Image.open(image_path).convert("RGB")
    gray = image.convert("L")
    array = np.asarray(gray)
    mask = array < 65
    rows = mask.mean(axis=1)
    cols = mask.mean(axis=0)
    ys = np.where(rows > 0.35)[0]
    xs = np.where(cols > 0.12)[0]
    if len(ys) > 30 and len(xs) > 30:
        box = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
        region = image.crop(box)
        if ImageStat.Stat(region.convert("L")).mean[0] < 135:
            image.paste(ImageOps.invert(region), box)
            note["inverted"] = True
            note["preprocess"] = "dark_panel_inverted"
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    note["image"] = str(dest)
    return note


def recognize(image_path: Path, backend: str) -> dict:
    if backend == "none":
        return {"text": "", "lines": [], "error": "OCR backend is none", "backend": backend}
    if backend == "paddle":
        return _paddle(image_path)
    if backend == "rapidocr":
        return _rapidocr(image_path)
    return {"text": "", "lines": [], "error": f"unknown OCR backend {backend}", "backend": backend}


def _paddle(image_path: Path) -> dict:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        return {
            "text": "",
            "lines": [],
            "error": f"PaddleOCR is not importable ({type(exc).__name__}: {exc}). No cloud OCR is used.",
            "backend": "paddle",
        }
    try:
        try:
            engine = PaddleOCR(
                lang="en",
                ocr_version="PP-OCRv4",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
                enable_mkldnn=False,
                cpu_threads=2,
            )
        except TypeError:
            engine = PaddleOCR(lang="en")
        lines = _paddle_lines(engine, image_path)
    except Exception as exc:
        return {
            "text": "",
            "lines": [],
            "error": f"PaddleOCR failed ({type(exc).__name__}: {exc})",
            "backend": "paddle",
        }
    return {"text": rows_to_text(lines), "lines": lines, "error": "", "backend": "paddle"}


def _paddle_lines(engine, image_path: Path) -> list[dict]:
    lines = []
    if hasattr(engine, "predict"):
        results = engine.predict(str(image_path))
        for result in results or []:
            texts = list(result.get("rec_texts", []))
            boxes = result.get("rec_boxes")
            if boxes is None:
                boxes = result.get("rec_polys", [])
            polys = result.get("rec_polys")
            if polys is None:
                polys = [None] * len(texts)
            for text, score, box, poly in zip(texts, result.get("rec_scores", []), boxes, polys):
                line = {"text": str(text), "engine_score": float(score), "box": _box(box)}
                parsed_poly = _poly(poly)
                if parsed_poly:
                    line["poly"] = parsed_poly
                lines.append(line)
        return lines
    results = engine.ocr(str(image_path), cls=False)
    for page in results or []:
        for item in page or []:
            box, rec = item[0], item[1]
            text, score = rec[0], rec[1]
            lines.append({"text": str(text), "engine_score": float(score), "box": _box(box)})
    return lines


def _rapidocr(image_path: Path) -> dict:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        return {
            "text": "",
            "lines": [],
            "error": f"rapidocr is not installed ({type(exc).__name__}: {exc})",
            "backend": "rapidocr",
        }
    try:
        engine = RapidOCR()
        results, _ = engine(str(image_path))
    except Exception as exc:
        return {"text": "", "lines": [], "error": f"rapidocr failed ({type(exc).__name__}: {exc})", "backend": "rapidocr"}
    lines = []
    for box, text, score in results or []:
        line = {"text": str(text), "engine_score": float(score), "box": _box(box)}
        parsed_poly = _poly(box)
        if parsed_poly:
            line["poly"] = parsed_poly
        lines.append(line)
    return {"text": rows_to_text(lines), "lines": lines, "error": "", "backend": "rapidocr"}


def _box(box) -> list[float]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    if box and isinstance(box[0], (list, tuple)):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]
    if len(box) >= 4:
        return [float(v) for v in box[:4]]
    return [0.0, 0.0, 0.0, 0.0]


def _poly(poly) -> list[list[float]] | None:
    if poly is None:
        return None
    if hasattr(poly, "tolist"):
        poly = poly.tolist()
    if len(poly) == 4 and all(isinstance(point, (list, tuple)) and len(point) == 2 for point in poly):
        return [[float(point[0]), float(point[1])] for point in poly]
    return None
