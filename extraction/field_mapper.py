"""Map a model proposal onto the assignment JSON.

Values survive only when a non-template page contains them. Prescriptions and
bank details are linked by identity_resolver or kept in the audit. Printed
money totals are not reconciled to a different line sum.
"""

from __future__ import annotations

import re
from decimal import Decimal

from extraction.identity_resolver import bank_link_decision, names_match, prescription_decision
from validation.normalizer import (
    find_date,
    find_labeled_date,
    find_phrase,
    format_amount,
    ground_amount,
    ground_compact,
    ground_int,
    ground_loose,
    ground_text,
    labeled_amounts,
    joined,
    normalize_date,
    parse_amount,
    span_is_on_page,
    stable_unique,
    sum_amounts,
)

# Brief sample placeholders. They are excluded when their only support is a
# template page. A real page that actually prints one of these can still keep it.
TEMPLATE_SENTINELS = {"62042", "66666"}

POLICY_VERSION = "2026-11-07.3"
LIST_SPLIT_RE = re.compile(r"\s*[,;]\s*")

ADMISSION_RE = re.compile(
    r"(date of admission|admission date|\bdoa\b|admitted on)",
    re.I,
)
DISCHARGE_RE = re.compile(
    r"(date of discharge|discharge date|\bdod\b|discharged on)",
    re.I,
)
DISCHARGE_TYPE_RE = re.compile(r"(discharge type|type of discharge)", re.I)
ROOM_START_RE = re.compile(r"(start date|from date|check-?in date)", re.I)
ROOM_END_RE = re.compile(
    r"(end date|to date|check-?out date|till date|until date)",
    re.I,
)
TPA_RE = re.compile(r"\btpa\b", re.I)
VISIT_RE = re.compile(r"date of visit\s*[:\-]?\s*(.+)", re.I)
STAY_RE = re.compile(r"length of stay\s*[:\-]?\s*(.+)", re.I)
SUBTOTAL_RE = re.compile(
    r"(?:subtotal|total medical bills)\s*[:\-]?\s*(?:inr|rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.I,
)
ROOM_TOTAL_RE = re.compile(
    r"(?:room total|total room amount)\s*[:\-]?\s*(?:inr|rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.I,
)
NUMBER_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
TOTAL_ROW_RE = re.compile(r"^(subtotal|total|total medical bills|grand total)\b", re.I)

def policy_snapshot(bank_policy: str) -> dict:
    return {
        "version": POLICY_VERSION,
        "bank_policy": bank_policy,
        "dates": "labeled_admission_and_discharge_only",
        "room_dates": "labeled_room_dates_only",
        "pharmacy": "link_only_when_patient_named_on_same_page_and_no_other_person",
        "money": "preserve_printed_components_do_not_reconcile_line_sum",
        "icd": "exact_source_span_only",
        "tpa": "require_tpa_label",
        "ifsc_directory": "not_consulted",
        "grounding": "clear_when_no_span",
        "grounding_fallbacks": "whitespace_insensitive_span; comma_list_split_each_item_grounded",
        "history_overlap": "history_item_dropped_when_its_span_is_kept_as_allergy_or_medication",
        "bill_labels": "need_amount_plus_group_or_category; labels_moved_to_printed_table_column_when_one_row_matches",
        "template": "template_pages_cannot_support_values",
        "template_sentinels": sorted(TEMPLATE_SENTINELS),
    }


def apply_policies(
    bundle: dict,
    pages: dict[int, str],
    *,
    bank_policy: str,
    allow_money: bool,
    observation_pages: dict[int, str] | None = None,
) -> tuple[dict, dict]:
    """Return `(data, audit_fragment)` for the assignment schema."""
    evidence: list[dict] = []
    dropped: list[dict] = []
    warnings: list[dict] = []
    allowed: dict[str, set[int]] = {
        key: set(value) for key, value in (bundle.get("allowed") or {}).items()
    }

    template_pages = {int(page) for page in (bundle.get("template_pages") or [])}

    def allow(key: str) -> set[int]:
        return set(allowed.get(key, set())) - template_pages

    hospital = _ground_object(
        bundle.get("hospital") or {},
        pages,
        allow("hospital"),
        "data.hospital",
        evidence,
        dropped,
        date_fields=set(),
    )
    doctor = _ground_object(
        bundle.get("doctor") or {},
        pages,
        allow("doctor"),
        "data.doctor",
        evidence,
        dropped,
        date_fields=set(),
    )
    patient = _ground_object(
        bundle.get("patient") or {},
        pages,
        allow("patient"),
        "data.patient",
        evidence,
        dropped,
        date_fields={"dob"},
    )
    _apply_tpa_rule(patient, pages, allow("patient"), evidence, dropped, warnings)

    history = _ground_dict(
        bundle.get("medicalhistory") or {},
        pages,
        allow("history"),
        "data.medicalDetails.medicalhistory",
        "Disease",
        evidence,
        dropped,
    )
    medications = _ground_dict(
        bundle.get("current_medications") or {},
        pages,
        allow("history"),
        "data.medicalDetails.current_medications",
        "Medication",
        evidence,
        dropped,
    )
    allergies = _ground_dict(
        bundle.get("allergies") or {},
        pages,
        allow("history"),
        "data.medicalDetails.allergies",
        "Allergy",
        evidence,
        dropped,
    )

    diagnosis = _ground_diagnoses(
        bundle.get("diagnosis") or [],
        pages,
        allow("diagnosis"),
        evidence,
        dropped,
    )
    symptoms = _ground_strings(
        bundle.get("symptoms") or [],
        pages,
        allow("symptoms"),
        "data.medicalDetails.symptoms",
        evidence,
        dropped,
    )
    history = _drop_history_overlaps(history, evidence, dropped)
    treatment_parts, treatment_misses = _ground_items(bundle.get("treatment_taken") or "", pages, allow("treatment"))
    treatment = "; ".join(text for text, _hit in treatment_parts)
    if treatment_parts:
        for text, hit in treatment_parts:
            evidence.append({"field": "data.medicalDetails.treatment_taken", "value": text, **hit})
        for miss in treatment_misses:
            dropped.append({"field": "data.medicalDetails.treatment_taken", "value": miss, "reason": "list item has no supporting span"})
    elif (bundle.get("treatment_taken") or "").strip():
        dropped.append(
            {
                "field": "data.medicalDetails.treatment_taken",
                "value": bundle.get("treatment_taken"),
                "reason": "no supporting span",
            }
        )

    discharge_type = _labeled_text(
        bundle.get("dischargeType") or "",
        pages,
        allow("dischargeType"),
        DISCHARGE_TYPE_RE,
        "data.medicalDetails.dischargeType",
        "no discharge-type label",
        evidence,
        dropped,
    )

    claim_pages = allow("claim")
    doa = _labeled_date(
        bundle.get("admission_date") or "",
        pages,
        claim_pages,
        ADMISSION_RE,
        "data.claim.doa",
        "no explicit admission-date label",
        evidence,
        dropped,
    )
    dod = _labeled_date(
        bundle.get("discharge_date") or "",
        pages,
        claim_pages,
        DISCHARGE_RE,
        "data.claim.dod",
        "no explicit discharge-date label; stay length and signature are not used",
        evidence,
        dropped,
    )

    opd_kept, opd_quarantine = _split_opd(
        bundle,
        pages,
        allow("opd"),
        patient.get("name", ""),
        evidence,
        dropped,
        warnings,
    )

    bank_out, bank_quarantine = _split_bank(
        bundle,
        pages,
        allow("bank"),
        patient.get("name", ""),
        bank_policy,
        evidence,
        dropped,
        warnings,
    )

    bills = _ground_bills(bundle.get("bills") or [], pages, allow("bills"), evidence, dropped)
    _check_bill_columns(bills, pages, allow("bills"), warnings)
    rooms = _ground_rooms(bundle.get("room_invoice") or [], pages, allow("room"), evidence, dropped, warnings)

    claimed, money = _money(
        pages,
        allow("bills"),
        allow("room"),
        bills,
        rooms,
        bundle.get("model_total") or "",
        allow_money,
        evidence,
        warnings,
    )

    observations = _observations(observation_pages if observation_pages is not None else pages)

    data = {
        "hospital": hospital,
        "doctor": doctor,
        "patient": patient,
        "claim": {"doa": doa, "dod": dod, "total_claimed_amount": claimed},
        "bills": bills,
        "medicalDetails": {
            "dischargeType": discharge_type,
            "patientAddress": {
                "address": patient.get("address", ""),
                "city": patient.get("city", ""),
                "state": patient.get("state", ""),
                "pinCode": patient.get("pin", ""),
            },
            "opd_details": opd_kept,
            "Patient_bankinfo": bank_out,
            "medicalhistory": history,
            "current_medications": medications,
            "allergies": allergies,
            "diagnosis": diagnosis,
            "symptoms": symptoms,
            "treatment_taken": treatment,
        },
        "room_invoice": rooms,
    }
    if any(patient.get(k) for k in ("address", "city", "state", "pin")):
        evidence.append(
            {
                "field": "data.medicalDetails.patientAddress",
                "value": data["medicalDetails"]["patientAddress"],
                "reason": "copied_from_grounded_patient_address",
            }
        )
    _scrub_template_sentinels(data, bundle, pages, template_pages, evidence, dropped, warnings)
    _annotate_evidence(evidence, bundle.get("page_meta") or {})
    _annotate_quarantine(opd_quarantine, bundle.get("page_meta") or {})
    _verify_spans(evidence, pages, dropped)
    audit = {
        "policy": policy_snapshot(bank_policy),
        "evidence": evidence,
        "dropped": dropped,
        "warnings": warnings,
        "observations": observations,
        "bank_decision": bank_quarantine,
        "quarantine": {
            "prescriptions": opd_quarantine,
            "bank": None
            if not bank_quarantine or bank_quarantine.get("linked_to_patient")
            else bank_quarantine,
        },
        "money": money,
    }
    return data, audit


def _observations(pages: dict[int, str]) -> list[dict]:
    found = []
    for page in sorted(pages):
        for line in pages[page].splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            visit = VISIT_RE.search(stripped)
            if visit:
                found.append(
                    {
                        "kind": "visit_date",
                        "page": page,
                        "span": stripped,
                        "copied_to_claim": False,
                        "reason": "visit date is not an admission or discharge date",
                    }
                )
            stay = STAY_RE.search(stripped)
            if stay:
                found.append(
                    {
                        "kind": "length_of_stay",
                        "page": page,
                        "span": stripped,
                        "copied_to_claim": False,
                        "reason": "length of stay does not set discharge or room end date",
                    }
                )
            if re.fullmatch(r"date\s*[:\-]\s*.+", stripped, flags=re.I) and not re.search(
                r"admission|discharge|visit|birth", stripped, flags=re.I
            ):
                found.append(
                    {
                        "kind": "unlabeled_date",
                        "page": page,
                        "span": stripped,
                        "copied_to_claim": False,
                        "reason": "unlabeled date, including a signature date, is not doa, dod, or a room date",
                    }
                )
    return found


def _apply_tpa_rule(patient, pages, allowed, evidence, dropped, warnings):
    value = patient.get("tpa_id", "")
    if not value:
        return
    blob = joined(pages, allowed)
    if TPA_RE.search(blob):
        return
    patient["tpa_id"] = ""
    evidence[:] = [item for item in evidence if item.get("field") != "data.patient.tpa_id"]
    dropped.append(
        {
            "field": "data.patient.tpa_id",
            "value": value,
            "reason": "value is not labeled as a TPA id; insurance or policy ids are not copied into tpa_id",
        }
    )
    warnings.append(
        {
            "code": "tpa_not_labeled",
            "message": "A patient id was present without a TPA label and was left empty.",
        }
    )


def _split_opd(bundle, pages, allowed, patient_name, evidence, dropped, warnings):
    empty = {
        "reportingComplaint": [],
        "diagnosis": [],
        "investigations": [],
        "pharmacy": [],
    }
    groups = {
        "reportingComplaint": bundle.get("reportingComplaint") or [],
        "diagnosis": bundle.get("opd_diagnosis") or [],
        "investigations": bundle.get("investigations") or [],
    }
    kept = {key: [] for key in empty}
    quarantined_items = []
    for key, values in groups.items():
        for value in values:
            text, hit = ground_text(str(value), pages, allowed)
            if not hit:
                if str(value).strip():
                    dropped.append(
                        {"field": f"data.medicalDetails.opd_details.{key}", "value": value, "reason": "no supporting span"}
                    )
                continue
            ok, reason = prescription_decision(pages[hit["page"]], patient_name)
            item = {"field": key, "value": text, **hit, "link_reason": reason}
            if ok:
                kept[key].append(text)
                evidence.append({"field": f"data.medicalDetails.opd_details.{key}", "value": text, **hit})
            else:
                quarantined_items.append(item)

    drugs_kept = []
    drugs_quarantined = []
    for index, drug in enumerate(bundle.get("pharmacy") or []):
        name, hit = ground_text((drug or {}).get("name", ""), pages, allowed)
        if not hit:
            if (drug or {}).get("name", "").strip():
                dropped.append(
                    {
                        "field": "data.medicalDetails.opd_details.pharmacy",
                        "value": drug.get("name"),
                        "reason": "drug name has no supporting span",
                    }
                )
            continue
        cleaned = {
            "name": name,
            "dosageFrequencyPerDay": ground_int(drug.get("dosageFrequencyPerDay"), pages, {hit["page"]}),
            "dosagefrequencyTotalDays": ground_int(drug.get("dosagefrequencyTotalDays"), pages, {hit["page"]}),
            "count": ground_int(drug.get("count"), pages, {hit["page"]}),
        }
        ok, reason = prescription_decision(pages[hit["page"]], patient_name)
        record = {"value": cleaned, **hit, "link_reason": reason, "index": index}
        if ok:
            drugs_kept.append((hit["page"], hit["start"], index, cleaned))
            evidence.append({"field": "data.medicalDetails.opd_details.pharmacy", "value": cleaned, **hit})
        else:
            drugs_quarantined.append(record)

    drugs_kept.sort(key=lambda item: (item[0], item[1], item[2]))
    kept["pharmacy"] = [item[3] for item in drugs_kept]
    for key in ("reportingComplaint", "diagnosis", "investigations"):
        kept[key] = stable_unique(kept[key])

    quarantine = None
    if quarantined_items or drugs_quarantined:
        rx_name, rx_hit = ground_text(bundle.get("rx_patient_name") or "", pages, allowed)
        quarantine = {
            "reason": "Prescription content is not linked to the main patient, so it was not merged.",
            "unverified_link": True,
            "rx_patient_name_grounded": rx_name,
            "rx_patient_name_evidence": rx_hit,
            "items": quarantined_items,
            "pharmacy": drugs_quarantined,
        }
        warnings.append(
            {
                "code": "prescription_quarantined",
                "message": (
                    "Prescription content was left out of the patient pharmacy because the page "
                    "does not safely link to the patient. See the audit quarantine. The link is unverified."
                ),
            }
        )
    elif (bundle.get("rx_patient_name") or "").strip():
        rx_name, rx_hit = ground_text(bundle.get("rx_patient_name") or "", pages, allowed)
        if rx_name and not names_match(rx_name, patient_name):
            warnings.append(
                {
                    "code": "prescription_name_differs_but_no_items_kept",
                    "message": "A different prescription name was grounded and no medicines were merged.",
                }
            )
    return kept, quarantine


def _split_bank(bundle, pages, allowed, patient_name, bank_policy, evidence, dropped, warnings):
    empty = {"bank_name": "", "account_number": "", "bank_address": "", "ifsi_code": ""}
    holder, holder_hit = ground_text(bundle.get("account_holder") or "", pages, allowed)
    proposed = {
        "bank_name": bundle.get("bank_name") or "",
        "account_number": bundle.get("account_number") or "",
        "bank_address": bundle.get("bank_address") or "",
        "ifsi_code": bundle.get("ifsi_code") or "",
    }
    grounded = {}
    hits = {}
    for key, value in proposed.items():
        if key in {"account_number", "ifsi_code"}:
            text, hit = ground_compact(value, pages, allowed)
        else:
            text, hit = ground_text(value, pages, allowed)
        if hit:
            grounded[key] = text
            hits[key] = hit
        elif str(value).strip():
            dropped.append({"field": f"bank.{key}", "value": value, "reason": "no supporting span on bank pages"})
    if (bundle.get("account_holder") or "").strip() and not holder_hit:
        dropped.append(
            {
                "field": "bank.account_holder",
                "value": bundle.get("account_holder"),
                "reason": "account holder text is not on the bank pages; the patient name is not used as a substitute",
            }
        )

    link, link_reason, match = bank_link_decision(holder, patient_name, bank_policy, bool(grounded))

    quarantine = None
    if grounded or holder:
        quarantine_body = {
            "account_holder": holder,
            "account_holder_evidence": holder_hit,
            "fields": grounded,
            "evidence": hits,
            "name_match": match,
            "policy": bank_policy,
            "linked_to_patient": link,
            "reason": link_reason,
            "unverified_link": True,
        }
        if link:
            for key, value in grounded.items():
                evidence.append({"field": f"data.medicalDetails.Patient_bankinfo.{key}", "value": value, **hits[key]})
            if holder_hit:
                evidence.append(
                    {
                        "field": "audit.bank.account_holder",
                        "value": holder,
                        **holder_hit,
                        "note": "holder is audit context and is not an output field",
                    }
                )
            return {**empty, **grounded}, quarantine_body
        quarantine = quarantine_body
        warnings.append(
            {
                "code": "bank_not_linked",
                "message": (
                    "Bank fields were extracted from the page and kept in the audit only. "
                    "Patient_bankinfo is empty because the holder is not verified as the patient. "
                    "A different holder can be a third party. The link is unverified."
                ),
            }
        )
        return empty, quarantine
    return empty, None


def _money(pages, bill_pages, room_pages, bills, rooms, model_total, allow_money, evidence, warnings):
    money = {
        "allow_applied": allow_money,
        "printed_medical_subtotal": "",
        "printed_medical_evidence": None,
        "printed_room_total": "",
        "printed_room_evidence": None,
        "line_sum": "",
        "derived_claim_total": "",
        "model_total_grounded": "",
    }
    line_sum = sum_amounts([row.get("amount", "") for row in bills])
    money["line_sum"] = format_amount(line_sum) if bills else ""

    sub_hits = labeled_amounts(pages, bill_pages, SUBTOTAL_RE)
    unique_subs = {hit["amount"] for hit in sub_hits}
    if len(unique_subs) == 1:
        money["printed_medical_subtotal"] = format_amount(sub_hits[0]["amount"])
        money["printed_medical_evidence"] = {k: v for k, v in sub_hits[0].items() if k != "amount"}
    elif len(unique_subs) > 1:
        warnings.append(
            {
                "code": "ambiguous_printed_subtotal",
                "message": "More than one distinct printed medical subtotal was found. None was chosen.",
            }
        )

    room_hits = labeled_amounts(pages, room_pages, ROOM_TOTAL_RE)
    unique_rooms = {hit["amount"] for hit in room_hits}
    if len(unique_rooms) == 1:
        money["printed_room_total"] = format_amount(room_hits[0]["amount"])
        money["printed_room_evidence"] = {k: v for k, v in room_hits[0].items() if k != "amount"}
    elif rooms:
        grounded_totals = []
        for row in rooms:
            amount = parse_amount(row.get("total_room_amount", ""))
            if amount is not None:
                grounded_totals.append(amount)
        if len(set(grounded_totals)) == 1:
            money["printed_room_total"] = format_amount(grounded_totals[0])
            money["printed_room_evidence"] = {
                "source": "grounded room total_room_amount that appears on the room page",
            }

    if money["printed_medical_subtotal"] and money["line_sum"]:
        printed = parse_amount(money["printed_medical_subtotal"])
        if printed is not None and printed != line_sum:
            warnings.append(
                {
                    "code": "printed_subtotal_mismatch",
                    "message": (
                        f"Printed medical subtotal {money['printed_medical_subtotal']} "
                        f"!= sum of grounded line amounts {money['line_sum']} "
                        f"(difference {format_amount(printed - line_sum)}). "
                        "The printed subtotal is kept. Line items were not changed to reconcile."
                    ),
                    "printed_subtotal": money["printed_medical_subtotal"],
                    "line_sum": money["line_sum"],
                    "difference": format_amount(printed - line_sum),
                }
            )

    for index, row in enumerate(rooms):
        rate = parse_amount(row.get("rate", ""))
        days = parse_amount(row.get("total_days", ""))
        discount = parse_amount(row.get("discount", "")) or Decimal("0")
        total = parse_amount(row.get("total_room_amount", ""))
        if rate is not None and days is not None and total is not None:
            expected = rate * days - discount
            if expected != total:
                warnings.append(
                    {
                        "code": "room_formula_mismatch",
                        "message": (
                            f"Room row {index}: rate * days - discount = {format_amount(expected)} "
                            f"but the printed room total is {format_amount(total)}. The printed total is kept."
                        ),
                    }
                )

    claimed = ""
    if allow_money and money["printed_medical_subtotal"] and money["printed_room_total"]:
        total = parse_amount(money["printed_medical_subtotal"]) + parse_amount(money["printed_room_total"])
        claimed = format_amount(total)
        money["derived_claim_total"] = claimed
        evidence.append(
            {
                "field": "data.claim.total_claimed_amount",
                "value": claimed,
                "derivation": "printed_medical_subtotal + printed_room_total",
                "inputs": [
                    money["printed_medical_subtotal"],
                    money["printed_room_total"],
                ],
                "not_a_printed_grand_total": True,
                "note": "Sum of sourced printed components. Not reconciled to the line-item sum.",
            }
        )
        model_amount, model_hit = ground_amount(model_total, pages, set(bill_pages) | set(room_pages))
        if model_hit:
            money["model_total_grounded"] = format_amount(model_amount)
            if format_amount(model_amount) != claimed:
                warnings.append(
                    {
                        "code": "model_total_differs_from_printed_components",
                        "message": (
                            f"Model total {money['model_total_grounded']} was not used. "
                            f"Printed components sum to {claimed}."
                        ),
                    }
                )
    elif (model_total or "").strip():
        dropped_reason = (
            "claim total was not replaced with a line-item reconciliation, and printed components were incomplete"
        )
        if not allow_money:
            dropped_reason = "money derivation is off because a required bill or room section did not succeed"
        dropped = {
            "field": "data.claim.total_claimed_amount",
            "value": model_total,
            "reason": dropped_reason,
        }
        # The caller passes `dropped` for field clearing. Record it as a warning too.
        warnings.append({"code": "claim_total_not_set", **dropped})
    return claimed, money


def _ground_bills(rows, pages, allowed, evidence, dropped):
    kept = []
    for index, row in enumerate(rows):
        group = (row or {}).get("bill_group", "")
        category = (row or {}).get("bill_category", "")
        if TOTAL_ROW_RE.search(group.strip()) or TOTAL_ROW_RE.search(category.strip()):
            dropped.append({"field": "data.bills", "value": group or category, "reason": "subtotal row is not a line item"})
            continue
        group_text, group_hit = ground_text(group, pages, allowed)
        category_text, category_hit = ground_text(category, pages, allowed)
        amount, amount_hit = ground_amount((row or {}).get("amount", ""), pages, allowed)
        if not amount_hit or not (group_hit or category_hit):
            if group.strip() or category.strip() or str((row or {}).get("amount", "")).strip():
                dropped.append(
                    {
                        "field": "data.bills",
                        "value": {"bill_group": group, "bill_category": category, "amount": (row or {}).get("amount", "")},
                        "reason": "line needs a grounded line amount and a grounded bill group or bill category",
                    }
                )
            continue
        item = {
            "invoice_date": "",
            "bill_group": group_text if group_hit else "",
            "bill_category": category_text if category_hit else "",
            "amount": format_amount(amount),
            "bill_number": "",
            "discount": "",
            "quantity": "",
            "remarks": "",
        }
        for key in ("bill_number", "remarks"):
            text, hit = ground_text((row or {}).get(key, ""), pages, allowed)
            if hit:
                item[key] = text
        date_text = normalize_date((row or {}).get("invoice_date", ""))
        if date_text and find_date(pages, date_text, allowed):
            item["invoice_date"] = date_text
        elif (row or {}).get("invoice_date", "").strip():
            dropped.append(
                {
                    "field": "data.bills.invoice_date",
                    "value": row.get("invoice_date"),
                    "reason": "invoice date has no supporting span",
                }
            )
        for key in ("discount", "quantity"):
            parsed, hit = ground_amount((row or {}).get(key, ""), pages, allowed)
            if hit:
                item[key] = format_amount(parsed)
        hit = {"page": amount_hit["page"], "start": amount_hit["start"], "span": amount_hit["span"]}
        kept.append((hit["page"], hit["start"], index, item, hit))
        evidence.append({"field": "data.bills", "value": item, **hit})
    kept.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in kept]


def _table_blocks(page_text: str) -> list[tuple[dict[str, int], list[list[str]]]]:
    """Header-to-index maps and same-width rows from `[TABLE]` blocks on a page."""
    blocks = []
    for block in re.findall(r"\[TABLE\]\n(.*?)(?:\n\n|\Z)", page_text or "", flags=re.S):
        header, rows = None, []
        for line in block.splitlines():
            cells = [" ".join(cell.split()) for cell in line.split(" | ")]
            names = {re.sub(r"\(.*?\)", "", cell).strip().lower(): index for index, cell in enumerate(cells)}
            if "bill category" in names and "bill group" in names:
                if header:
                    blocks.append((header, rows))
                header, rows = names, []
            elif header and len(cells) == len(header):
                rows.append(cells)
        if header:
            blocks.append((header, rows))
    return blocks


def _check_bill_columns(bills, pages, allowed, warnings):
    """Put each model label under the printed column it actually sits in.

    Only labels the model returned are moved, and only when exactly one printed
    row matches the line total and both labels.
    """
    tables = [block for page in sorted(allowed) for block in _table_blocks(pages.get(page, ""))]
    if not tables:
        return
    reassigned = 0
    for item in bills:
        labels = {item["bill_group"].lower(), item["bill_category"].lower()} - {""}
        matches = []
        for header, rows in tables:
            total_at = header.get("total")
            if total_at is None:
                continue
            for cells in rows:
                if parse_amount(cells[total_at]) != parse_amount(item["amount"]):
                    continue
                printed = {cells[header["bill category"]].lower(), cells[header["bill group"]].lower()}
                if labels and labels <= printed:
                    matches.append((header, cells))
        if len(matches) != 1:
            continue
        header, cells = matches[0]
        group_cell, category_cell = cells[header["bill group"]], cells[header["bill category"]]
        new_group = group_cell if group_cell.lower() in labels else ""
        new_category = category_cell if category_cell.lower() in labels else ""
        if (new_group, new_category) != (item["bill_group"], item["bill_category"]):
            item["bill_group"], item["bill_category"] = new_group, new_category
            reassigned += 1
    if reassigned:
        warnings.append(
            {
                "code": "bill_labels_moved_to_printed_columns",
                "message": (
                    f"{reassigned} bill line(s) had bill_group and bill_category under the wrong printed column. "
                    "Each label was moved to the column it is printed under in the table header. No text was changed or added."
                ),
            }
        )


def _ground_rooms(rows, pages, allowed, evidence, dropped, warnings):
    kept = []
    for index, row in enumerate(rows):
        room_type, type_hit = ground_text((row or {}).get("room_type", ""), pages, allowed)
        total, total_hit = ground_amount((row or {}).get("total_room_amount", ""), pages, allowed)
        if not type_hit and not total_hit:
            if any(str((row or {}).get(k, "")).strip() for k in row or {}):
                dropped.append({"field": "data.room_invoice", "value": row, "reason": "room row is not grounded"})
            continue
        item = {key: "" for key in (
            "room_type", "room_category", "start_date", "end_date", "rate", "quantity",
            "discount", "total_days", "total_room_amount",
        )}
        if type_hit:
            item["room_type"] = room_type
        category, cat_hit = ground_text((row or {}).get("room_category", ""), pages, allowed)
        if cat_hit:
            item["room_category"] = category
        for key in ("rate", "quantity", "discount", "total_days"):
            parsed, hit = ground_amount((row or {}).get(key, ""), pages, allowed)
            if hit:
                item[key] = format_amount(parsed)
        if total_hit:
            item["total_room_amount"] = format_amount(total)
        for key, label_re, label in (
            ("start_date", ROOM_START_RE, "no labeled room start date"),
            ("end_date", ROOM_END_RE, "no labeled room end date; stay length and signature are not used"),
        ):
            raw = (row or {}).get(key, "")
            normalized = normalize_date(raw)
            if not str(raw).strip():
                continue
            hit = find_labeled_date(pages, allowed, label_re, normalized) if normalized else None
            if hit:
                item[key] = normalized
                evidence.append({"field": f"data.room_invoice.{key}", "value": normalized, **hit})
            else:
                dropped.append({"field": f"data.room_invoice.{key}", "value": raw, "reason": label})
                warnings.append({"code": "room_date_not_used", "message": f"{key}: {label}", "value": raw})
        anchor = total_hit or type_hit
        kept.append((anchor["page"], anchor["start"], index, item))
        evidence.append({"field": "data.room_invoice", "value": item, "page": anchor["page"], "span": anchor["span"]})
    kept.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in kept]


def _ground_diagnoses(rows, pages, allowed, evidence, dropped):
    kept = []
    for index, row in enumerate(rows):
        name, hit = _ground_one((row or {}).get("name", ""), pages, allowed)
        if not hit:
            if (row or {}).get("name", "").strip() or (row or {}).get("icd", "").strip():
                dropped.append({"field": "data.medicalDetails.diagnosis", "value": row, "reason": "diagnosis name has no supporting span"})
            continue
        icd = ""
        raw_icd = str((row or {}).get("icd", "") or "").strip()
        icd_hit = find_phrase(pages, raw_icd, allowed) if raw_icd else None
        if icd_hit:
            icd = pages[icd_hit["page"]][icd_hit["start"]:icd_hit["end"]]
            evidence.append({"field": "data.medicalDetails.diagnosis.icd", "value": icd, **icd_hit})
        elif raw_icd:
            dropped.append(
                {
                    "field": "data.medicalDetails.diagnosis.icd",
                    "value": raw_icd,
                    "reason": "ICD kept only when that code is printed",
                }
            )
        item = {"name": name, "icd": icd}
        kept.append((hit["page"], hit["start"], index, item))
        evidence.append({"field": "data.medicalDetails.diagnosis", "value": item, **hit})
    kept.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in kept]


def _ground_one(value, pages, allowed):
    text, hit = ground_text(value, pages, allowed)
    if hit:
        return text, hit
    return ground_loose(value, pages, allowed)


def _ground_items(value, pages, allowed):
    """Ground a value, or each item of a comma/semicolon list, as page spans.

    Adjacent list items are re-joined when the page prints them as one item.
    Returns `(parts, misses)`; a part is `(text, hit)`.
    """
    raw = " ".join(str(value or "").split())
    if not raw:
        return [], []
    text, hit = _ground_one(raw, pages, allowed)
    if hit:
        return [(text, hit)], []
    tokens = LIST_SPLIT_RE.split(raw)
    separators = LIST_SPLIT_RE.findall(raw)
    if len(tokens) < 2:
        return [], [raw]
    parts, misses, seen = [], [], set()
    start = 0
    while start < len(tokens):
        for end in range(len(tokens), start, -1):
            candidate = tokens[start]
            for offset in range(start + 1, end):
                candidate += separators[offset - 1] + tokens[offset]
            if not candidate.strip():
                continue
            text, hit = _ground_one(candidate, pages, allowed)
            if hit:
                where = (hit["page"], hit["start"])
                if where not in seen:
                    seen.add(where)
                    parts.append((text, hit))
                start = end
                break
        else:
            if tokens[start].strip():
                misses.append(tokens[start])
            start += 1
    return parts, misses


def _drop_history_overlaps(history, evidence, dropped):
    field = "data.medicalDetails.medicalhistory"
    others = [
        item
        for item in evidence
        if str(item.get("field", "")).startswith(
            ("data.medicalDetails.allergies.", "data.medicalDetails.current_medications.")
        )
        and item.get("start") is not None
    ]
    kept = []
    for item in [entry for entry in evidence if str(entry.get("field", "")).startswith(field + ".")]:
        clash = next(
            (
                other
                for other in others
                if other.get("page") == item.get("page")
                and item.get("start") is not None
                and other["start"] < item["end"]
                and item["start"] < other["end"]
            ),
            None,
        )
        if clash:
            evidence.remove(item)
            dropped.append(
                {
                    "field": field,
                    "value": item.get("value"),
                    "reason": f"same page span is already kept as {clash['field']}",
                }
            )
        else:
            kept.append(item)
    if len(kept) == len(history):
        return history
    out = {}
    for number, item in enumerate(kept, start=1):
        label = f"Disease{number}"
        item["field"] = f"{field}.{label}"
        out[label] = item["value"]
    return out


def _ground_dict(values, pages, allowed, field, prefix, evidence, dropped):
    found = []
    seen = set()
    index = 0
    for key, value in (values or {}).items():
        key_text = " ".join(str(key or "").split())
        value_text = " ".join(str(value or "").split())
        parts, misses = [], []
        if key_text and value_text:
            text, hit = _ground_one(f"{key_text} {value_text}", pages, allowed)
            if hit:
                parts = [(text, hit)]
        if not parts and value_text:
            parts, misses = _ground_items(value_text, pages, allowed)
        if not parts and not value_text and key_text:
            parts, _misses = _ground_items(key_text, pages, allowed)
        if parts:
            for miss in misses:
                dropped.append({"field": field, "value": miss, "reason": "list item has no supporting span", "model_key": key})
        elif value_text:
            dropped.append({"field": field, "value": value, "reason": "no supporting span", "model_key": key})
        for text, hit in parts:
            where = (hit["page"], hit["start"])
            if where in seen:
                continue
            seen.add(where)
            found.append((hit["page"], hit["start"], index, text, hit))
            index += 1
    found.sort(key=lambda item: (item[0], item[1], item[2]))
    out = {}
    for n, item in enumerate(found, start=1):
        label = f"{prefix}{n}"
        out[label] = item[3]
        evidence.append({"field": f"{field}.{label}", "value": item[3], **item[4]})
    return out


def _ground_strings(values, pages, allowed, field, evidence, dropped):
    found = []
    for index, value in enumerate(values or []):
        parts, misses = _ground_items(str(value), pages, allowed)
        for offset, (text, hit) in enumerate(parts):
            found.append((hit["page"], hit["start"], index + offset / 100, text, hit))
        for miss in misses if parts else ([value] if str(value).strip() else []):
            dropped.append({"field": field, "value": miss, "reason": "no supporting span"})
    found.sort(key=lambda item: (item[0], item[1], item[2]))
    out = []
    for item in found:
        if item[3] not in out:
            out.append(item[3])
            evidence.append({"field": field, "value": item[3], **item[4]})
    return out


def _ground_object(values, pages, allowed, field, evidence, dropped, date_fields):
    out = {}
    for key, value in (values or {}).items():
        if key in date_fields:
            normalized = normalize_date(str(value))
            hit = find_date(pages, normalized, allowed) if normalized else None
            if hit:
                out[key] = normalized
                evidence.append({"field": f"{field}.{key}", "value": normalized, **hit})
            else:
                out[key] = ""
                if str(value).strip():
                    dropped.append({"field": f"{field}.{key}", "value": value, "reason": "no supporting date span"})
            continue
        if key in {"pin"}:
            text, hit = ground_compact(str(value), pages, allowed)
        else:
            text, hit = ground_text(str(value), pages, allowed)
        out[key] = text
        if hit:
            evidence.append({"field": f"{field}.{key}", "value": text, **hit})
        elif str(value).strip():
            dropped.append({"field": f"{field}.{key}", "value": value, "reason": "no supporting span"})
    return out


def _labeled_date(value, pages, allowed, label_re, field, reason, evidence, dropped):
    normalized = normalize_date(value)
    hit = find_labeled_date(pages, allowed, label_re, normalized) if normalized else None
    if hit and span_is_on_page(pages.get(hit["page"], ""), hit["span"], hit["start"], hit["end"]):
        evidence.append({"field": field, "value": normalized, **hit})
        return normalized
    if str(value).strip():
        dropped.append({"field": field, "value": value, "reason": reason})
    return ""


def _labeled_text(value, pages, allowed, label_re, field, reason, evidence, dropped):
    text, hit = ground_text(value, pages, allowed)
    if hit and _label_on_same_line(pages.get(hit["page"], ""), label_re, hit["span"]):
        evidence.append({"field": field, "value": text, **hit})
        return text
    if str(value).strip():
        dropped.append({"field": field, "value": value, "reason": reason})
    return ""


def _label_on_same_line(page_text: str, label_re, span: str) -> bool:
    for line in (page_text or "").splitlines():
        if label_re.search(line) and span_is_on_page(line, span):
            return True
    return False




def _scrub_template_sentinels(data, bundle, pages, template_pages, evidence, dropped, warnings):
    if not template_pages:
        return
    safe = set(pages) - set(template_pages)
    removed = 0

    def is_sentinel(value) -> bool:
        amount = parse_amount(value)
        return amount is not None and format_amount(amount) in TEMPLATE_SENTINELS

    def supported_outside(value) -> bool:
        _amount, hit = ground_amount(value, pages, safe)
        return hit is not None

    proposed = []
    for row in bundle.get("bills") or []:
        if is_sentinel((row or {}).get("amount")):
            proposed.append((row or {}).get("amount"))
    for row in bundle.get("room_invoice") or []:
        if is_sentinel((row or {}).get("total_room_amount")):
            proposed.append((row or {}).get("total_room_amount"))
    if is_sentinel(bundle.get("model_total")):
        proposed.append(bundle.get("model_total"))
    if any(not supported_outside(value) for value in proposed):
        removed += 1

    bills = []
    for row in data["bills"]:
        if is_sentinel(row.get("amount")) and not supported_outside(row.get("amount")):
            dropped.append({"field": "data.bills", "value": row, "reason": "template sample amount excluded"})
            removed += 1
            continue
        bills.append(row)
    data["bills"] = bills
    rooms = []
    for row in data["room_invoice"]:
        if is_sentinel(row.get("total_room_amount")) and not supported_outside(row.get("total_room_amount")):
            dropped.append({"field": "data.room_invoice", "value": row, "reason": "template sample room amount excluded"})
            removed += 1
            continue
        rooms.append(row)
    data["room_invoice"] = rooms
    claimed = data["claim"]["total_claimed_amount"]
    if is_sentinel(claimed) and not supported_outside(claimed):
        data["claim"]["total_claimed_amount"] = ""
        dropped.append(
            {
                "field": "data.claim.total_claimed_amount",
                "value": claimed,
                "reason": "template sample amount excluded",
            }
        )
        removed += 1
    if removed:
        warnings.append(
            {
                "code": "template_contamination_excluded",
                "message": (
                    "Sample amounts 62042 and 66666 were excluded as instruction-page contamination. "
                    "This is not an invoice conflict."
                ),
            }
        )


def _attach_ocr_observation(item, meta):
    """Copy an OCR engine score only when the engine produced one for this span.

    The score is a recognition statistic. It is not factual or medical correctness,
    and it is omitted when the engine did not supply one.
    """
    span = str(item.get("span") or "")
    method = str(item.get("method") or "")
    if not method.startswith("ocr") or not span:
        return
    for line in meta.get("lines") or []:
        if span not in str(line.get("text") or ""):
            continue
        if line.get("box") is not None:
            item["box"] = line["box"]
        if "engine_score" in line:
            item["ocr_engine_score"] = line["engine_score"]
            item["ocr_engine_score_meaning"] = "recognition only; not factual or medical correctness"
        return


def _annotate_evidence(evidence, page_meta):
    for item in evidence:
        page = item.get("page")
        meta = page_meta.get(page) or page_meta.get(str(page) if page is not None else "", {}) or {}
        if item.get("derivation"):
            item["method"] = "derivation"
            item["validation"] = "derived_from_printed_components"
            item["identity"] = "not_applicable"
            continue
        if item.get("reason") == "copied_from_grounded_patient_address":
            item["method"] = "copy"
            item["validation"] = "copied_from_patient"
            item["identity"] = "record_patient"
            continue
        method = meta.get("method") or "text-layer"
        item["method"] = method
        item["validation"] = "span_found_in_source" if item.get("span") else "unchecked"
        field = str(item.get("field") or "")
        if "pharmacy" in field or "opd_details" in field:
            item["identity"] = "linked_to_patient"
        elif "Patient_bankinfo" in field or field.startswith("audit.bank"):
            item["identity"] = "linked_to_patient"
        elif field.startswith("data.patient"):
            item["identity"] = "record_patient"
        else:
            item["identity"] = "not_applicable"
        _attach_ocr_observation(item, meta)


def _annotate_quarantine(quarantine, page_meta):
    if not quarantine:
        return
    for key in ("items", "pharmacy"):
        for item in quarantine.get(key) or []:
            item["identity"] = "not_linked"
            item["validation"] = "span_found_in_source" if item.get("span") else "unchecked"
            page = item.get("page")
            meta = page_meta.get(page) or {}
            item["method"] = meta.get("method") or "text-layer"
            _attach_ocr_observation(item, meta)


def _verify_spans(evidence, pages, dropped):
    """Drop quotes that are not actually on the cited page. Model citations are not evidence."""
    kept = []
    for item in list(evidence):
        if item.get("derivation") or item.get("method") == "copy":
            item["span_verified"] = False
            item["span_check"] = "not_a_page_quote"
            kept.append(item)
            continue
        span = item.get("span")
        page = item.get("page")
        page_text = pages.get(page, "") if page is not None else ""
        if span and span_is_on_page(page_text, span, item.get("start"), item.get("end")):
            item["span_verified"] = True
            item["span_check"] = "cited_page_text"
            kept.append(item)
            continue
        dropped.append(
            {
                "field": item.get("field"),
                "value": item.get("value"),
                "reason": "quoted span is not on the cited page; a model assertion was not accepted",
            }
        )
    evidence[:] = kept
