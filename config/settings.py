"""Runtime settings. Page ranges are explicit. Nothing here calls a network."""

from __future__ import annotations

from dataclasses import dataclass

PIPELINE_VERSION = "2.1.0"
DEFAULT_MODEL = "mistral:7b"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_DPI = 200
# Temperature 0 and a fixed seed are request options only.
# They do not make completions identical across machines or batch sizes.
GENERATION_SEED = 42

ASSIGNMENT_PAGE_SPEC = "6-12"
ASSIGNMENT_FORCE_OCR = frozenset({7, 8, 9, 11})
# Pages reviewed as the primary patient's hospital record. A page here may link
# clinical content to the patient without repeating the name, but only when it
# names no other person. Page 8 (outside prescription) and 9 (cheque) are not here.
ASSIGNMENT_PRIMARY_RECORD_PAGES = frozenset({6, 7, 10, 11, 12})


@dataclass(frozen=True)
class SectionSpec:
    name: str
    model_name: str
    pages: tuple[int, ...]


def generation_options(num_ctx: int) -> dict:
    return {
        "temperature": 0,
        "seed": GENERATION_SEED,
        "top_k": 1,
        "num_ctx": num_ctx,
        "num_predict": 2000,
    }


def parse_page_spec(spec: str, count: int) -> list[int]:
    raw = (spec or "all").strip().lower()
    if raw in {"", "all", "*"}:
        wanted = list(range(1, count + 1))
    else:
        wanted = []
        for part in raw.split(","):
            piece = part.strip()
            if not piece:
                continue
            if "-" in piece:
                start_s, end_s = piece.split("-", 1)
                start, end = int(start_s), int(end_s)
                if end < start:
                    raise ValueError(f"page range {piece!r} is reversed")
                wanted.extend(range(start, end + 1))
            else:
                wanted.append(int(piece))
    ordered = []
    seen = set()
    for page in wanted:
        if 1 <= page <= count and page not in seen:
            seen.add(page)
            ordered.append(page)
    return ordered


def select_pages(profile: str, page_spec: str | None, count: int) -> list[int]:
    if page_spec:
        return parse_page_spec(page_spec, count)
    if profile == "clarion-assignment":
        return parse_page_spec(ASSIGNMENT_PAGE_SPEC, count)
    return parse_page_spec("all", count)


def section_plan(profile: str, available: list[int], *, smoke: bool) -> list[SectionSpec]:
    present = set(available)
    if profile == "clarion-assignment":
        raw = (
            SectionSpec("identity", "identity", (6, 12)),
            SectionSpec("history", "history", (7,)),
            SectionSpec("opd", "opd", (8,)),
            SectionSpec("visit_opd", "visit_opd", (10,)),
            SectionSpec("bank", "bank", (9,)),
            SectionSpec("visit", "visit", (10,)),
            SectionSpec("bills", "bills", (11,)),
            SectionSpec("room", "room", (11,)),
        )
    else:
        raw = (SectionSpec("record", "record", tuple(available)),)
    planned = []
    for spec in raw:
        pages = tuple(page for page in spec.pages if page in present)
        if pages:
            planned.append(SectionSpec(spec.name, spec.model_name, pages))
    if smoke:
        return planned[:1]
    return planned


def primary_record_pages(profile: str) -> set[int]:
    if profile == "clarion-assignment":
        return set(ASSIGNMENT_PRIMARY_RECORD_PAGES)
    return set()


def force_ocr_pages(profile: str) -> set[int]:
    if profile == "clarion-assignment":
        return set(ASSIGNMENT_FORCE_OCR)
    return set()


def allowed_keys(section_name: str) -> tuple[str, ...]:
    mapping = {
        "identity": ("hospital", "doctor", "patient"),
        "history": ("history",),
        "opd": ("opd",),
        "visit_opd": ("opd",),
        "bank": ("bank",),
        "visit": ("claim", "diagnosis", "symptoms", "treatment", "dischargeType"),
        "bills": ("bills",),
        "room": ("room",),
        "record": (
            "hospital",
            "doctor",
            "patient",
            "history",
            "opd",
            "bank",
            "bills",
            "room",
            "claim",
            "diagnosis",
            "symptoms",
            "treatment",
            "dischargeType",
        ),
    }
    return mapping[section_name]
