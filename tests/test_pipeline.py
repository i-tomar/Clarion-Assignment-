"""Synthetic and mocked tests. No real medical PDF and no live model."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.make_synthetic_pdf import OTHER, RECORD, TEMPLATE, write_pdf
from extraction.field_mapper import apply_policies
from extraction.ollama_transport import OllamaError
from main import main
from processors.document_classifier import classify
from processors.ocr_processor import recognize, rows_to_text
from processors.pdf_processor import Document, backend_name
from processors.table_extractor import words_from_pdf
from schemas.medical_schema import (
    Bill,
    Claim,
    Doctor,
    Drug,
    Dx,
    GeneralSection,
    Hospital,
    Patient,
    Room,
)
from validation.consistency_checker import check_consistency
from validation.normalizer import span_is_on_page
from validation.schema_validator import validate_schema


def _bundle(**kwargs):
    base = {
        "hospital": {},
        "doctor": {},
        "patient": {},
        "admission_date": "",
        "discharge_date": "",
        "bills": [],
        "dischargeType": "",
        "reportingComplaint": [],
        "opd_diagnosis": [],
        "investigations": [],
        "pharmacy": [],
        "rx_patient_name": "",
        "account_holder": "",
        "bank_name": "",
        "account_number": "",
        "bank_address": "",
        "ifsi_code": "",
        "medicalhistory": {},
        "current_medications": {},
        "allergies": {},
        "diagnosis": [],
        "symptoms": [],
        "treatment_taken": "",
        "room_invoice": [],
        "model_total": "",
        "allowed": {},
        "template_pages": set(),
        "page_meta": {},
    }
    base.update(kwargs)
    return base


def _run(tmp_path, pdf, responses, extra=None, runs=1):
    fake = FakeChat(responses * runs)
    code = main(
        ["--workdir", str(tmp_path), "--backend", "none", "--template-mode", "auto", str(pdf), *(extra or [])],
        chat_func=fake,
        digest_func=lambda host, model: "sha256:fixture",
    )
    return code, fake


class FakeChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"content": item, "prompt_eval_count": 1, "eval_count": 1}


def _general_json():
    model = GeneralSection(
        hospital=Hospital(name="North Clinic", city="Pune"),
        doctor=Doctor(name="Dr Meera Iyer"),
        patient=Patient(name="Asha Rao", dob="15/03/1980", city="Pune", tpa_id="TPA-100", phone="9999911111"),
        admission_date="01/02/2024",
        discharge_date="03/02/2024",
        visit_date="05/02/2024",
        signature_date="09/02/2024",
        bills=[
            Bill(bill_group="Consult", amount="20000"),
            Bill(bill_group="Labs", amount="10400"),
            Bill(bill_group="Example", amount="62042"),
        ],
        symptoms=["Thirst"],
        treatment_taken="Oral fluids",
        diagnosis=[Dx(name="Mild dehydration", icd="Z99")],
        document_patient_name="Rohan Das",
        pharmacy=[Drug(name="Tab. Saline"), Drug(name="Tab. Mintol")],
        account_holder="Asha Rao",
        bank_name="Example Bank",
        account_number="555544443333",
        ifsi_code="EXAM0001234",
        room_invoice=[
            Room(
                room_type="General",
                room_category="Ward",
                rate="4000",
                total_days="10",
                discount="1000",
                total_room_amount="39000",
                end_date="09/02/2024",
            )
        ],
        total_claimed_amount="69400",
    )
    return model.model_dump_json()


def test_schema_defaults_and_extra_keys():
    claim = Claim()
    assert claim.success is False
    assert claim.message == ""
    assert claim.data.patient.name == ""
    assert claim.data.patient.phone == ""
    assert claim.data.bills == []
    assert claim.data.room_invoice == []
    assert claim.data.medicalDetails.symptoms == []
    assert claim.data.medicalDetails.diagnosis == []
    other = Claim()
    claim.data.medicalDetails.symptoms.append("x")
    assert other.data.medicalDetails.symptoms == []
    with pytest.raises(ValidationError):
        Hospital.model_validate({"name": "North Clinic", "invented": "x"})
    empty = claim.model_dump(mode="json")
    assert validate_schema(empty)["schema_valid"] is True
    assert "evidence" not in empty
    assert "audit" not in empty["data"]


def test_span_must_be_on_the_cited_page_whitespace_only():
    pages = {1: "Patient Name: Asha   Rao\nCity: Pune"}
    data, audit = apply_policies(
        _bundle(patient={"name": "Asha Rao", "city": "Pune", "phone": "9999911111"}, allowed={"patient": {1}}),
        pages,
        bank_policy="link_if_name_match",
        allow_money=False,
    )
    assert data["patient"]["name"] == "Asha Rao"
    assert data["patient"]["phone"] == ""
    name_evidence = [item for item in audit["evidence"] if item["field"] == "data.patient.name"]
    assert name_evidence
    item = name_evidence[0]
    assert item["span_verified"] is True
    assert item["span_check"] == "cited_page_text"
    assert pages[item["page"]][item["start"]:item["end"]] == item["span"]
    assert span_is_on_page(pages[1], "Asha Rao") is True
    assert span_is_on_page(pages[1], "the model says this is on page 1") is False
    assert "confidence" not in item
    assert "ocr_engine_score" not in item


def test_dates_are_not_derived_and_must_be_labeled():
    pages = {
        1: "Date of Visit: 05/02/2024\nLength of Stay: 10 days\nDate: 09/02/2024\nSymptoms: Thirst",
    }
    data, audit = apply_policies(
        _bundle(
            admission_date="05/02/2024",
            discharge_date="09/02/2024",
            symptoms=["Thirst"],
            room_invoice=[{"room_type": "General", "end_date": "09/02/2024", "total_room_amount": "10"}],
            allowed={"claim": {1}, "symptoms": {1}, "room": {1}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    assert data["claim"]["doa"] == ""
    assert data["claim"]["dod"] == ""
    assert data["room_invoice"][0]["end_date"] == ""
    assert any(item["kind"] == "visit_date" for item in audit["observations"])
    assert all(item["copied_to_claim"] is False for item in audit["observations"])


def test_explicit_admission_and_discharge_are_kept():
    pages = {1: "Date of admission: 01/02/2024\nDate of discharge: 03/02/2024\n"}
    data, _audit = apply_policies(
        _bundle(admission_date="01/02/2024", discharge_date="03/02/2024", allowed={"claim": {1}}),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    assert data["claim"]["doa"] == "01/02/2024"
    assert data["claim"]["dod"] == "03/02/2024"


def test_template_amounts_are_contamination_not_a_bill_conflict():
    pages = {
        1: "EXAMPLE SCHEMA ONLY\nBill group: Example\nAmount: 62042\nRoom total: 66666",
        2: "Bill group: Labs\nAmount: 10400",
    }
    data, audit = apply_policies(
        _bundle(
            bills=[
                {"bill_group": "Example", "amount": "62042"},
                {"bill_group": "Labs", "amount": "10400"},
            ],
            room_invoice=[{"room_type": "Private", "total_room_amount": "66666"}],
            allowed={"bills": {1, 2}, "room": {1, 2}},
            template_pages={1},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    amounts = [row["amount"] for row in data["bills"]]
    assert "62042" not in amounts
    assert "10400" in amounts
    assert data["room_invoice"] == [] or all(row["total_room_amount"] != "66666" for row in data["room_invoice"])
    codes = [item["code"] for item in audit["warnings"]]
    assert "template_contamination_excluded" in codes
    assert "printed_subtotal_mismatch" not in codes


def test_sentinel_amount_can_remain_on_a_non_template_page():
    pages = {1: "Bill group: Consult\nAmount: 62042"}
    data, audit = apply_policies(
        _bundle(
            bills=[{"bill_group": "Consult", "amount": "62042"}],
            allowed={"bills": {1}},
            template_pages=set(),
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    assert data["bills"][0]["amount"] == "62042"
    assert all(item["code"] != "template_contamination_excluded" for item in audit["warnings"])


def test_printed_30900_is_kept_when_lines_sum_to_30400():
    pages = {
        1: "\n".join(
            [
                "Bill group: Consult",
                "Amount: 20000",
                "Bill group: Labs",
                "Amount: 10400",
                "Subtotal: 30900",
                "Total Medical Bills: 30900",
                "Room type: General",
                "Room total: 39000",
                "Rate: 4000",
                "Total days: 10",
                "Discount: 1000",
                "Total room amount: 39000",
            ]
        )
    }
    data, audit = apply_policies(
        _bundle(
            bills=[
                {"bill_group": "Consult", "amount": "20000"},
                {"bill_group": "Labs", "amount": "10400"},
            ],
            room_invoice=[
                {
                    "room_type": "General",
                    "rate": "4000",
                    "total_days": "10",
                    "discount": "1000",
                    "total_room_amount": "39000",
                }
            ],
            model_total="69400",
            allowed={"bills": {1}, "room": {1}},
        ),
        pages,
        bank_policy="never",
        allow_money=True,
    )
    assert data["claim"]["total_claimed_amount"] == "69900"
    assert [row["amount"] for row in data["bills"]] == ["10400", "20000"] or set(
        row["amount"] for row in data["bills"]
    ) == {"10400", "20000"}
    warning = next(item for item in audit["warnings"] if item["code"] == "printed_subtotal_mismatch")
    assert warning["printed_subtotal"] == "30900"
    assert warning["line_sum"] == "30400"
    assert warning["difference"] == "500"
    factual = check_consistency(
        {"data": data, "success": True, "message": "Success"},
        {"warnings": audit["warnings"], "sections": []},
    )
    assert factual["schema_valid"] is None
    assert any(item["code"] == "line_sum_differs_from_claimed" for item in factual["factual_warnings"])
    assert validate_schema({"data": data, "success": True, "message": "Success"})["schema_valid"] is True


def test_bill_line_needs_amount_and_one_grounded_label():
    pages = {1: "Bill Category | Bill Group | Total\nRadiology | Chest X-ray | 1,000\nSubtotal: 1,000"}
    data, audit = apply_policies(
        _bundle(
            bills=[
                {"bill_category": "Radiology", "bill_group": "", "amount": "1000"},
                {"bill_category": "Invented", "bill_group": "Also invented", "amount": "1000"},
                {"bill_category": "Subtotal", "bill_group": "", "amount": "1000"},
            ],
            allowed={"bills": {1}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    assert data["bills"] == [
        {
            "invoice_date": "",
            "bill_group": "",
            "bill_category": "Radiology",
            "amount": "1000",
            "bill_number": "",
            "discount": "",
            "quantity": "",
            "remarks": "",
        }
    ]
    reasons = [item["reason"] for item in audit["dropped"] if item["field"] == "data.bills"]
    assert any("grounded bill group or bill category" in reason for reason in reasons)
    assert any("subtotal" in reason for reason in reasons)


def test_swapped_bill_labels_follow_the_printed_table_header():
    pages = {
        1: "Medical Bills\n\n[TABLE]\n"
        "Bill Category | Bill Group | Unit Price (INR) | Total (INR)\n"
        "Emergency Services | Admission Fee | 5,000 | 5,000\n"
        "Radiology | Chest X-ray | 1,000 | 1,000"
    }
    data, audit = apply_policies(
        _bundle(
            bills=[
                {"bill_group": "Emergency Services", "bill_category": "Admission Fee", "amount": "5000"},
                {"bill_group": "Radiology", "bill_category": "", "amount": "1000"},
            ],
            allowed={"bills": {1}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    by_amount = {row["amount"]: row for row in data["bills"]}
    assert by_amount["5000"]["bill_group"] == "Admission Fee"
    assert by_amount["5000"]["bill_category"] == "Emergency Services"
    assert by_amount["1000"]["bill_group"] == ""
    assert by_amount["1000"]["bill_category"] == "Radiology"
    assert any(item["code"] == "bill_labels_moved_to_printed_columns" for item in audit["warnings"])


def test_primary_visit_page_opd_is_kept_while_outside_prescription_stays_quarantined():
    pages = {
        6: "Name: Asha Rao\nCity: Pune",
        8: "Smile Clinic\nTab. Mintol 1 x 5 days",
        10: "Symptoms: Thirst and fatigue\nMedications Prescribed:\nAmlodipine 5 mg daily\n"
        "Epoetin alfa 4000 units weekly\nFollow up in 1 week\nLab Tests Ordered:\nComplete Blood Count (CBC)",
    }
    data, audit = apply_policies(
        _bundle(
            patient={"name": "Asha Rao"},
            reportingComplaint=["Thirst and fatigue"],
            investigations=["Complete Blood Count (CBC)"],
            pharmacy=[
                {"name": "Amlodipine 5 mg", "dosageFrequencyPerDay": 3, "dosagefrequencyTotalDays": 1, "count": 30},
                {"name": "Epoetin alfa 4000 units", "dosageFrequencyPerDay": 1},
                {"name": "Tab. Mintol", "dosagefrequencyTotalDays": 5},
            ],
            allowed={"patient": {6}, "opd": {8, 10}},
            primary_record_pages={6, 10},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    opd = data["medicalDetails"]["opd_details"]
    assert opd["reportingComplaint"] == ["Thirst and fatigue"]
    assert opd["investigations"] == ["Complete Blood Count (CBC)"]
    assert opd["pharmacy"] == [
        {"name": "Amlodipine 5 mg", "dosageFrequencyPerDay": 1, "dosagefrequencyTotalDays": 0, "count": 0},
        {"name": "Epoetin alfa 4000 units", "dosageFrequencyPerDay": 0, "dosagefrequencyTotalDays": 0, "count": 0},
    ]
    quarantined = audit["quarantine"]["prescriptions"]["pharmacy"]
    assert [item["value"]["name"] for item in quarantined] == ["Tab. Mintol"]
    assert all(item["page"] == 8 for item in quarantined)
    kept = [item for item in audit["evidence"] if "opd_details" in item["field"]]
    assert kept and all(item["page"] == 10 and item["span_verified"] for item in kept)


def test_past_procedures_are_not_diseases_and_room_cell_is_completed():
    pages = {
        7: "KnownConditions:\nHypertension\nPastSurgeriesorMajorTreatments:\nFistulacreationfordialysis(2022)\n",
        11: "Room Charges\n\n[TABLE]\nRoom Type | Room Category | Total (INR)\nPrivate Room | Deluxe | 39,000",
    }
    data, audit = apply_policies(
        _bundle(
            medicalhistory={"a": "Hypertension", "b": "Fistula creation for dialysis (2022)"},
            room_invoice=[{"room_type": "Private", "room_category": "Deluxe", "total_room_amount": "39000"}],
            allowed={"history": {7}, "room": {11}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    assert data["medicalDetails"]["medicalhistory"] == {"Disease1": "Hypertension"}
    assert [item["value"] for item in audit["past_procedures"]] == ["Fistula creation for dialysis (2022)"]
    assert data["room_invoice"][0]["room_type"] == "Private Room"


def test_ocr_override_needs_an_exact_line_and_is_recorded():
    from main import _apply_overrides

    record = {"number": 7, "ocr_text": "x", "lines": [{"text": "Abc5oo", "engine_score": 0.9, "box": [0, 0, 10, 10]}]}
    overrides = [
        {"pdf_sha256": "sha", "page": 7, "ocr_line": "Abc5oo", "reviewed_line": "Abc500"},
        {"pdf_sha256": "sha", "page": 7, "ocr_line": "Missing", "reviewed_line": "X"},
        {"pdf_sha256": "other", "page": 7, "ocr_line": "Abc5oo", "reviewed_line": "Y"},
    ]
    _apply_overrides(record, overrides, "sha")
    assert record["lines"][0]["text"] == "Abc500"
    assert "engine_score" not in record["lines"][0]
    assert [item["status"] for item in record["overrides"]] == ["applied", "not_applied_0_matches"]


def test_unlinked_prescription_and_bank_are_not_merged():
    pages = {
        1: "Patient Name: Asha Rao\nTab. Saline",
        2: "Patient Name: Rohan Das\nTab. Mintol",
        3: "Account holder: Lila Bose\nBank name: Example Bank\nAccount number: 555544443333\nIFSC: EXAM0001234",
    }
    data, audit = apply_policies(
        _bundle(
            patient={"name": "Asha Rao"},
            pharmacy=[{"name": "Tab. Saline"}, {"name": "Tab. Mintol"}],
            rx_patient_name="Rohan Das",
            account_holder="Lila Bose",
            bank_name="Example Bank",
            account_number="555544443333",
            ifsi_code="EXAM0001234",
            allowed={"patient": {1}, "opd": {1, 2}, "bank": {3}},
        ),
        pages,
        bank_policy="link_if_name_match",
        allow_money=False,
    )
    assert [item["name"] for item in data["medicalDetails"]["opd_details"]["pharmacy"]] == ["Tab. Saline"]
    assert data["medicalDetails"]["Patient_bankinfo"]["account_number"] == ""
    assert audit["quarantine"]["bank"]["unverified_link"] is True
    assert audit["quarantine"]["prescriptions"]["unverified_link"] is True
    assert "fraud" not in json.dumps(audit).lower()
    forced, forced_audit = apply_policies(
        _bundle(
            patient={"name": "Asha Rao"},
            account_holder="Lila Bose",
            bank_name="Example Bank",
            account_number="555544443333",
            allowed={"patient": {1}, "bank": {3}},
        ),
        pages,
        bank_policy="always",
        allow_money=False,
    )
    assert forced["medicalDetails"]["Patient_bankinfo"]["account_number"] == "555544443333"
    assert forced_audit["bank_decision"]["linked_to_patient"] is True
    assert "unverified" in forced_audit["bank_decision"]["reason"]


def test_ocr_engine_score_is_not_invented(tmp_path):
    image = tmp_path / "scan.png"
    write_pdf(image.with_suffix(".pdf"), ["Scan"])
    # Recognize must not pretend success when Paddle is absent. When it is installed,
    # recognition is not run here so a unit test never loads or fetches weights.
    import importlib.util

    if importlib.util.find_spec("paddleocr") is None:
        result = recognize(image, "paddle")
        assert result["text"] == ""
        assert result["error"]
        assert "engine_score" not in result
    lines = [
        {"text": "Labs", "box": [10, 10, 40, 20], "engine_score": 0.42},
        {"text": "10400", "box": [50, 12, 90, 20], "engine_score": 0.91},
    ]
    assert "Labs" in rows_to_text(lines)
    pages = {1: "Bill group: Labs\nAmount: 10400"}
    _data, audit = apply_policies(
        _bundle(
            bills=[{"bill_group": "Labs", "amount": "10400"}],
            allowed={"bills": {1}},
            page_meta={1: {"method": "ocr", "lines": lines}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    bill_evidence = next(item for item in audit["evidence"] if item["field"] == "data.bills")
    assert bill_evidence["ocr_engine_score"] == 0.91
    assert "not factual" in bill_evidence["ocr_engine_score_meaning"]
    assert "confidence" not in bill_evidence


def _rotated_line(text, x, y, slope=0.035, height=26):
    width = 12 * len(text)
    top = y + x * slope
    return {
        "text": text,
        "engine_score": 0.9,
        "box": [x, top, x + width, top + width * slope + height],
        "poly": [[x, top], [x + width, top + width * slope], [x + width, top + width * slope + height], [x, top + height]],
    }


def test_rotated_table_rows_keep_wrapped_cells_together():
    from processors.table_extractor import tables_from_ocr_lines

    columns = (100, 320, 620, 800)
    lines = [_rotated_line("Medical Bills", 100, 20)]
    lines += [_rotated_line(text, x, 100) for text, x in zip(("Item", "Group", "Qty", "Total"), columns)]
    lines += [_rotated_line(text, x, 180) for text, x in zip(("Emergency", "Admission Fee", "1", "5,000"), columns)]
    lines.append(_rotated_line("Services", 100, 220))
    lines += [_rotated_line(text, x, 300) for text, x in zip(("Laboratory", "CBC Panel", "1", "1,500"), columns)]
    lines.append(_rotated_line("Tests", 100, 340))
    lines.append(_rotated_line("Subtotal: 6,500", 100, 420))
    rows = tables_from_ocr_lines(lines).splitlines()
    assert "Item | Group | Qty | Total" in rows
    assert "Emergency Services | Admission Fee | 1 | 5,000" in rows
    assert "Laboratory Tests | CBC Panel | 1 | 1,500" in rows
    assert "Subtotal: 6,500" in rows
    assert tables_from_ocr_lines([_rotated_line("Plain line", 100, y) for y in range(0, 400, 40)]) == ""


def test_history_items_are_split_grounded_and_not_duplicated():
    pages = {
        1: "KnownConditions:\nChronicKidneyDisease(CKD),Stage5(ES\nRD)\nHypertension\n"
        "Allergies:\nSulfadrugs\nCurrentMedications:\nAmlodipine5mgdaily\n"
    }
    data, audit = apply_policies(
        _bundle(
            medicalhistory={
                "conditions": "ChronicKidneyDisease(CKD),Stage5(ESRD),Hypertension",
                "allergies": "Sulfadrugs",
                "medications": "Amlodipine5mgdaily",
                "other": "Asthma",
            },
            allergies={"Sulfadrugs": ""},
            current_medications={"Amlodipine": "5mg daily"},
            allowed={"history": {1}},
        ),
        pages,
        bank_policy="never",
        allow_money=False,
    )
    details = data["medicalDetails"]
    assert list(details["medicalhistory"].values()) == ["ChronicKidneyDisease(CKD),Stage5(ESRD)", "Hypertension"]
    assert list(details["allergies"].values()) == ["Sulfadrugs"]
    assert list(details["current_medications"].values()) == ["Amlodipine 5mg daily"]
    assert any(item["value"] == "Asthma" for item in audit["dropped"])
    for item in audit["evidence"]:
        assert item["span_verified"] is True
        assert pages[1][item["start"]:item["end"]] == item["span"]


def test_native_pdf_text_and_template_classification(tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    write_pdf(pdf, [TEMPLATE, RECORD, OTHER])
    assert backend_name() == "pymupdf"
    document = Document(pdf)
    try:
        assert document.page_count == 3
        first = document.text(1)
        second = document.text(2)
    finally:
        document.close()
    assert "62042" in first
    assert "66666" in first
    assert classify(first)["template"] is True
    assert classify(second)["template"] is False
    assert "Asha Rao" in second
    assert "Asha Rao" in words_from_pdf(pdf, 2)
    assert classify("Scan")["needs_ocr"] is True


def test_mocked_pipeline_excludes_template_and_keeps_printed_total(tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    write_pdf(pdf, [TEMPLATE, RECORD, OTHER])
    code, fake = _run(tmp_path, pdf, [_general_json()])
    assert code == 0
    assert len(fake.calls) == 1
    prompt = fake.calls[0]["messages"][-1]["content"]
    assert "62042" not in prompt
    assert "66666" not in prompt
    assert "[PAGE 2]" in prompt
    assert "[PAGE 3]" in prompt
    claim = json.loads((tmp_path / "output" / "output.json").read_text())
    audit = json.loads((tmp_path / "output" / "audit.json").read_text())
    assert claim["success"] is True
    assert claim["data"]["patient"]["name"] == "Asha Rao"
    assert claim["data"]["patient"]["phone"] == ""
    assert claim["data"]["claim"]["dod"] == "03/02/2024"
    assert claim["data"]["claim"]["total_claimed_amount"] == "69900"
    assert "62042" not in json.dumps(claim["data"]["bills"])
    assert "66666" not in json.dumps(claim["data"]["room_invoice"])
    assert claim["data"]["room_invoice"][0]["end_date"] == ""
    assert [item["name"] for item in claim["data"]["medicalDetails"]["opd_details"]["pharmacy"]] == ["Tab. Saline"]
    assert claim["data"]["medicalDetails"]["diagnosis"][0]["icd"] == ""
    assert "evidence" not in claim
    assert any(item["code"] == "printed_subtotal_mismatch" for item in audit["warnings"])
    assert audit["inference"] == "fresh"
    for item in audit["evidence"]:
        if item.get("span_check") == "cited_page_text":
            assert item["span_verified"] is True


def test_cache_versus_fresh_and_digest(tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    write_pdf(pdf, [RECORD])
    body = _general_json()
    stale = tmp_path / "output" / "cache"
    stale.mkdir(parents=True)
    (stale / "identity-old.json").write_text(json.dumps({"hospital": {"name": "STALE SENTINEL"}}), encoding="utf-8")
    code, fake = _run(tmp_path, pdf, [body])
    assert code == 0
    assert len(fake.calls) == 1
    code, fake = _run(tmp_path, pdf, [body])
    assert code == 0
    assert fake.calls == []
    claim = json.loads((tmp_path / "output" / "output.json").read_text())
    assert "STALE SENTINEL" not in json.dumps(claim)
    audit = json.loads((tmp_path / "output" / "audit.json").read_text())
    assert audit["inference"] == "cached"
    assert audit["stale_cache_files_ignored"] >= 1
    code, fake = _run(tmp_path, pdf, [body], extra=["--fresh"])
    assert len(fake.calls) == 1
    assert json.loads((tmp_path / "output" / "run.json").read_text())["inference"] == "fresh"
    other = FakeChat([body])

    def other_digest(host, model):
        return "sha256:other"

    code = main(
        ["--workdir", str(tmp_path), "--backend", "none", str(pdf)],
        chat_func=other,
        digest_func=other_digest,
    )
    assert code == 0
    assert len(other.calls) == 1


def test_fresh_five_calls_model_cached_five_does_not(tmp_path):
    pdf = tmp_path / "one.pdf"
    write_pdf(pdf, [RECORD])
    body = _general_json()
    fresh = FakeChat([body] * 5)
    code = main(
        ["--workdir", str(tmp_path), "--backend", "none", "--repeatability", "--repeatability-runs", "5", str(pdf)],
        chat_func=fresh,
        digest_func=lambda host, model: "sha256:fixture",
    )
    assert code == 0
    assert len(fresh.calls) == 5
    report = json.loads((tmp_path / "output" / "repeatability.json").read_text())
    assert len(report["runs"]) == 5
    assert all(item["inference"] == "fresh" for item in report["runs"])
    assert all(item["response_cache"] == "not_read_not_written" for item in report["runs"])
    assert "do not guarantee" in report["note"]
    assert report["differing_fields"] == []
    assert len(list((tmp_path / "output" / "repeatability").glob("run-*.json"))) == 5
    cached = FakeChat([body] * 5)
    for index in range(5):
        main(
            ["--workdir", str(tmp_path / "cached"), "--backend", "none", str(pdf)],
            chat_func=cached,
            digest_func=lambda host, model: "sha256:fixture",
        )
    assert len(cached.calls) == 1
    assert json.loads((tmp_path / "cached" / "output" / "audit.json").read_text())["inference"] == "cached"


def test_invalid_json_retries_then_model_errors_are_visible(tmp_path):
    pdf = tmp_path / "one.pdf"
    write_pdf(pdf, [RECORD])
    good = _general_json()
    retry = FakeChat(["{", good])
    code = main(
        ["--workdir", str(tmp_path / "retry"), "--backend", "none", str(pdf)],
        chat_func=retry,
        digest_func=lambda host, model: "sha256:fixture",
    )
    assert code == 0
    assert len(retry.calls) == 2
    broken = FakeChat([OllamaError("model 'mistral:7b' not found (status code: 404)")])
    code = main(
        ["--workdir", str(tmp_path / "down"), "--backend", "none", str(pdf)],
        chat_func=broken,
        digest_func=lambda host, model: "sha256:fixture",
    )
    assert code == 2
    claim = json.loads((tmp_path / "down" / "output" / "output.json").read_text())
    assert claim["success"] is False
    assert "not found" in claim["message"]
    assert claim["data"]["patient"]["name"] == ""


def test_partial_ocr_does_not_report_success(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    write_pdf(pdf, [RECORD, "Scan"])
    code, _fake = _run(tmp_path, pdf, [_general_json()])
    assert code == 2
    claim = json.loads((tmp_path / "output" / "output.json").read_text())
    audit = json.loads((tmp_path / "output" / "audit.json").read_text())
    assert claim["success"] is False
    assert "OCR incomplete" in claim["message"]
    assert claim["data"]["patient"]["name"] == "Asha Rao"
    assert any(page["needs_ocr"] and page["ocr_error"] for page in audit["pages"])


def test_assignment_profile_does_not_silently_use_short_pdfs(tmp_path):
    pdf = tmp_path / "short.pdf"
    write_pdf(pdf, [TEMPLATE, RECORD])
    fake = FakeChat([])
    code = main(
        ["--workdir", str(tmp_path), "--profile", "clarion-assignment", "--backend", "none", str(pdf)],
        chat_func=fake,
        digest_func=lambda host, model: "sha256:fixture",
    )
    assert code == 2
    assert fake.calls == []
    claim = json.loads((tmp_path / "output" / "output.json").read_text())
    assert claim["success"] is False


def test_non_loopback_host_is_refused(tmp_path):
    code = main(["--host", "http://example.com", "--workdir", str(tmp_path)])
    assert code == 1


def test_digest_falls_back_to_tags_when_show_has_none(monkeypatch):
    import extraction.ollama_transport as transport

    calls = []

    def fake_local_json(url, body, timeout, action):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/api/show"):
            return {"details": {"quantization_level": "Q4_K_M"}}
        return {
            "models": [
                {"name": "other:latest", "model": "other:latest", "digest": "aaa"},
                {"name": "mistral:7b", "model": "mistral:7b", "digest": "bbb"},
            ]
        }

    monkeypatch.setattr(transport, "_local_json", fake_local_json)
    assert transport.fetch_model_digest("http://127.0.0.1:11434", "mistral:7b") == "bbb"
    assert calls == ["show", "tags"]
    with pytest.raises(OllamaError):
        transport.fetch_model_digest("http://127.0.0.1:11434", "absent:1")


def test_sample_structure_is_not_an_extraction():
    sample = Claim.model_validate(
        {
            "data": {},
            "success": False,
            "message": "SAMPLE STRUCTURE ONLY. Not an extraction. No model ran.",
        }
    ).model_dump(mode="json")
    assert validate_schema(sample)["schema_valid"] is True
    assert sample["success"] is False
    assert "Not an extraction" in sample["message"]
