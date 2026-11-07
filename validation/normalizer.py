"""Date, amount, and span normalization. Formatting only; it does not invent values."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

NUMBER_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")

def ground_text(value: str, pages: dict[int, str], allowed: set[int]):
    raw = (value or "").strip()
    if len(raw) < 2:
        return "", None
    hit = find_phrase(pages, raw, allowed)
    if not hit:
        return "", None
    span = pages[hit["page"]][hit["start"]:hit["end"]]
    return " ".join(span.split()), hit


def ground_loose(value: str, pages: dict[int, str], allowed: set[int]):
    """Match ignoring whitespace only. Every other character must be on the page.

    Needed for OCR of text printed without word gaps and for values that wrap
    across lines. The returned text keeps the model's spacing only when its
    non-whitespace characters equal the page span exactly.
    """
    raw = " ".join((value or "").split())
    key = re.sub(r"\s+", "", raw)
    if len(key) < 4:
        return "", None
    for page in sorted(allowed):
        body = pages.get(page, "")
        chars = []
        index_map = []
        for index, char in enumerate(body):
            if not char.isspace():
                chars.append(char.lower())
                index_map.append(index)
        at = "".join(chars).find(key.lower())
        if at < 0:
            continue
        start = index_map[at]
        end = index_map[at + len(key) - 1] + 1
        span = body[start:end]
        text = raw if re.sub(r"\s+", "", span) == key else " ".join(span.split())
        return text, {"page": page, "start": start, "end": end, "span": span, "match": "whitespace_insensitive"}
    return "", None


def ground_compact(value: str, pages: dict[int, str], allowed: set[int]):
    raw = re.sub(r"[^A-Za-z0-9]", "", value or "")
    if len(raw) < 2:
        return "", None
    for page in sorted(allowed):
        body = pages.get(page, "")
        compact = []
        index_map = []
        for index, char in enumerate(body):
            if char.isalnum():
                compact.append(char.lower())
                index_map.append(index)
        haystack = "".join(compact)
        at = haystack.find(raw.lower())
        if at < 0:
            continue
        start = index_map[at]
        end = index_map[at + len(raw) - 1] + 1
        return raw, {"page": page, "start": start, "end": end, "span": body[start:end]}
    return "", None


def ground_int(value, pages, allowed) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    if number == 0:
        return 0
    _amount, hit = ground_amount(str(number), pages, allowed)
    return number if hit else 0


def ground_amount(value, pages, allowed):
    amount = parse_amount(value)
    if amount is None:
        return None, None
    for page in sorted(allowed):
        body = pages.get(page, "")
        for match in NUMBER_RE.finditer(body):
            try:
                if Decimal(match.group(0).replace(",", "")) == amount:
                    return amount, {
                        "page": page,
                        "start": match.start(),
                        "end": match.end(),
                        "span": match.group(0),
                    }
            except InvalidOperation:
                continue
    return None, None


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def span_is_on_page(page_text: str, span: str, start=None, end=None) -> bool:
    """True only when the quoted span is on this page.

    Exact offsets win. Otherwise whitespace is collapsed and case is ignored.
    A model sentence that merely cites a page is not enough.
    """
    if not span or not str(span).strip() or not page_text:
        return False
    if start is not None and end is not None:
        try:
            if page_text[int(start):int(end)] == span:
                return True
        except (TypeError, ValueError):
            pass
    collapsed = collapse_ws(str(span))
    if len(collapsed) < 2:
        return False
    return collapsed in collapse_ws(page_text)


def find_labeled_date(pages, allowed, label_re, ddmmyyyy: str):
    """A date counts only on the same line as its label. Stay and signature lines do not qualify."""
    if not ddmmyyyy:
        return None
    for page in sorted(allowed):
        body = pages.get(page, "")
        offset = 0
        for line in body.splitlines(keepends=True):
            if label_re.search(line):
                hit = find_date({page: line}, ddmmyyyy, {page})
                if hit and span_is_on_page(line, hit["span"], hit["start"], hit["end"]):
                    return {
                        "page": page,
                        "start": offset + hit["start"],
                        "end": offset + hit["end"],
                        "span": hit["span"],
                    }
            offset += len(line)
    return None


def find_phrase(pages, value, allowed):
    tokens = [token for token in re.split(r"\s+", (value or "").strip()) if token]
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    regex = re.compile(pattern, re.I)
    for page in sorted(allowed):
        match = regex.search(pages.get(page, ""))
        if match:
            return {"page": page, "start": match.start(), "end": match.end(), "span": match.group(0)}
    return None


def find_date(pages, ddmmyyyy: str, allowed):
    if not ddmmyyyy:
        return None
    try:
        parsed = datetime.strptime(ddmmyyyy, "%d/%m/%Y")
    except ValueError:
        return None
    variants = [
        parsed.strftime("%d/%m/%Y"),
        parsed.strftime("%d-%m-%Y"),
        parsed.strftime("%d.%m.%Y"),
        f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}",
        f"{parsed.strftime('%B')} {parsed.day},{parsed.year}",
        f"{parsed.day} {parsed.strftime('%B')} {parsed.year}",
        f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}",
        f"{parsed.day} {parsed.strftime('%b')} {parsed.year}",
    ]
    for page in sorted(allowed):
        body = pages.get(page, "")
        for variant in variants:
            if variant[0].isdigit() and variant[2] in "/-.":
                match = re.search(rf"(?<!\d){re.escape(variant)}(?!\d)", body)
            else:
                match = re.search(re.escape(variant), body, flags=re.I)
            if match:
                return {"page": page, "start": match.start(), "end": match.end(), "span": match.group(0)}
    return None


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    formats = (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return ""


def label_present(pages, allowed, regex) -> bool:
    return bool(regex.search(joined(pages, allowed)))


def joined(pages, allowed) -> str:
    return "\n".join(pages.get(page, "") for page in sorted(allowed))


def labeled_amounts(pages, allowed, regex) -> list[dict]:
    hits = []
    for page in sorted(allowed):
        body = pages.get(page, "")
        for match in regex.finditer(body):
            amount = parse_amount(match.group(1))
            if amount is None:
                continue
            hits.append(
                {
                    "amount": amount,
                    "page": page,
                    "start": match.start(1),
                    "end": match.end(1),
                    "span": match.group(1),
                }
            )
    return hits


def parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def sum_amounts(values) -> Decimal:
    total = Decimal("0")
    for value in values:
        amount = parse_amount(value)
        if amount is not None:
            total += amount
    return total


def format_amount(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return format(value.quantize(Decimal("0.01")), "f")


def stable_unique(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
