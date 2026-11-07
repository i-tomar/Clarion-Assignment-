"""Link a prescription or bank document to the main patient, or quarantine it.

A different name is an unverified association.
"""
from __future__ import annotations

import re

TITLES = {"mr", "mrs", "ms", "miss", "dr", "smt", "shri"}

def page_link(page_text: str, patient_name: str, primary_record_page: bool = False) -> tuple[bool, str]:
    others = other_people(page_text, patient_name)
    if others:
        return False, "a different person is named on this page: " + "; ".join(others)
    if page_contains_name(page_text, patient_name):
        return True, "patient name appears on this page and no other person is labeled"
    if primary_record_page and name_tokens(patient_name):
        return True, "page is configured as part of the primary patient's record and names no other person"
    return False, "patient name does not appear on this page"


def other_people(text: str, patient_name: str) -> list[str]:
    found = []
    patterns = [
        re.compile(r"([A-Za-z ]{0,24})\bname[ \t]*[:\-][ \t]*([A-Za-z]{2,}(?:[ \t]+[A-Za-z]{2,})+)", re.I),
        re.compile(r"\b(?:mr|mrs|ms|miss)\.?[ \t]+([A-Za-z]{2,}(?:[ \t]+[A-Za-z]{2,})+)", re.I),
    ]
    skip = ("hospital", "doctor", "bank", "company", "clinic")
    candidates = []
    for match in patterns[0].finditer(text):
        prefix = match.group(1).lower()
        if any(word in prefix for word in skip):
            continue
        candidates.append(match.group(2).strip())
    for match in patterns[1].finditer(text):
        candidates.append(match.group(1).strip())
    for candidate in candidates:
        if not names_match(candidate, patient_name):
            if candidate not in found:
                found.append(candidate)
    return found


def page_contains_name(text: str, patient: str) -> bool:
    tokens = name_tokens(patient)
    if len(tokens) < 2:
        return False
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower())
    phrase = " ".join(tokens)
    if phrase in norm:
        return True
    if len(tokens) == 2 and f"{tokens[1]} {tokens[0]}" in norm:
        return True
    return False


def names_match(left: str, right: str) -> bool:
    a = name_tokens(left)
    b = name_tokens(right)
    if len(a) < 2 or len(b) < 2:
        return False
    return sorted(a) == sorted(b)


def name_tokens(value: str) -> list[str]:
    parts = re.findall(r"[a-z]+", (value or "").lower())
    return [part for part in parts if part not in TITLES and len(part) > 1]


def prescription_decision(page_text: str, patient_name: str, primary_record_page: bool = False) -> tuple[bool, str]:
    return page_link(page_text, patient_name, primary_record_page)


def bank_link_decision(holder: str, patient_name: str, policy: str, has_fields: bool) -> tuple[bool, str, bool]:
    match = bool(holder) and names_match(holder, patient_name)
    if policy == "always":
        return (
            bool(has_fields),
            "operator set bank policy to always; association is unverified unless the names match",
            match,
        )
    if policy == "never":
        return False, "bank policy never: extracted bank fields stay in the audit", match
    if match and has_fields:
        return True, "account holder matches the patient name", match
    return False, "account holder is not a verified match to the patient; bank fields stay in the audit", match
