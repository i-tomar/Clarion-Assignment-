"""Factual checks. They do not change the JSON and they are not schema validation.

Differences are warnings. A line-sum that disagrees with a printed subtotal
is reported and left as printed.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

PIN_RE = re.compile(r"\d{6}")
PAN_RE = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
IFSC_RE = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")
DATE_FIELDS = {"dob", "doa", "dod", "invoice_date", "start_date", "end_date"}


def check_consistency(claim: dict, audit: dict | None = None) -> dict:
    warnings: list[dict] = []
    data = claim.get("data") or {}
    patient = data.get("patient") or {}
    hospital = data.get("hospital") or {}
    bank = ((data.get("medicalDetails") or {}).get("Patient_bankinfo") or {})
    _format_warning(warnings, patient.get("pin", ""), PIN_RE, "patient.pin")
    _format_warning(warnings, hospital.get("pin", ""), PIN_RE, "hospital.pin")
    _format_warning(warnings, patient.get("pan_card", ""), PAN_RE, "patient.pan_card")
    _format_warning(warnings, bank.get("ifsi_code", ""), IFSC_RE, "Patient_bankinfo.ifsi_code")
    _date_warnings(warnings, patient)
    _date_warnings(warnings, data.get("claim") or {})
    for row in data.get("bills") or []:
        _date_warnings(warnings, row)
    for row in data.get("room_invoice") or []:
        _date_warnings(warnings, row)
        _room_formula(warnings, row)

    line_sum = _sum(row.get("amount") for row in data.get("bills") or [])
    room_sum = _sum(row.get("total_room_amount") for row in data.get("room_invoice") or [])
    claimed = _parse((data.get("claim") or {}).get("total_claimed_amount"))
    if claimed is not None and (data.get("bills") or data.get("room_invoice")):
        combined = line_sum + room_sum
        if combined != claimed:
            warnings.append(
                {
                    "code": "line_sum_differs_from_claimed",
                    "message": (
                        f"Grounded line amounts plus room totals equal {_fmt(combined)}; "
                        f"total_claimed_amount is {_fmt(claimed)}. "
                        "The checker does not rewrite either number. "
                        "A difference can be a printed subtotal that does not match its lines."
                    ),
                    "line_plus_room": _fmt(combined),
                    "claimed": _fmt(claimed),
                }
            )
    if audit:
        for item in audit.get("warnings") or []:
            warnings.append({"source": "audit", **item})
        failed = [
            section.get("name")
            for section in audit.get("sections") or []
            if str(section.get("status", "")).startswith("failed")
        ]
        if claim.get("success") and failed:
            warnings.append(
                {
                    "code": "success_flag_conflicts_with_audit",
                    "message": "success is true while the audit lists failed sections",
                    "sections": failed,
                }
            )
    return {"factual_warnings": warnings, "schema_valid": None}


def _format_warning(warnings, value, pattern, label):
    if value and not pattern.fullmatch(str(value)):
        warnings.append({"code": "format", "message": f"{label} does not match the expected pattern", "value": value})


def _date_warnings(warnings, obj: dict):
    for key, value in obj.items():
        if key in DATE_FIELDS and value:
            try:
                datetime.strptime(str(value), "%d/%m/%Y")
            except ValueError:
                warnings.append({"code": "date", "message": f"{key} is not DD/MM/YYYY", "value": value})


def _room_formula(warnings, row: dict):
    rate, days, total = _parse(row.get("rate")), _parse(row.get("total_days")), _parse(row.get("total_room_amount"))
    discount = _parse(row.get("discount")) or Decimal("0")
    if rate is not None and days is not None and total is not None:
        expected = rate * days - discount
        if expected != total:
            warnings.append(
                {
                    "code": "room_formula_mismatch",
                    "message": (
                        f"rate * days - discount = {_fmt(expected)} but printed total_room_amount is {_fmt(total)}. "
                        "The printed total is left unchanged."
                    ),
                }
            )


def _parse(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def _sum(values) -> Decimal:
    total = Decimal("0")
    for value in values:
        parsed = _parse(value)
        if parsed is not None:
            total += parsed
    return total


def _fmt(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return format(value, "f")
