"""Strict shape for the assignment JSON.

The brief's sample object is not valid JSON: curly quotes, missing commas,
and an invoice array with no key. Key names that look like typos are kept
on purpose (`ifsi_code`, `dosagefrequencyTotalDays`, `Patient_bankinfo`,
`medicalhistory`). The invoice array is named `bills`. Placeholder values
in the brief are not defaults. `success` defaults to false so a failed run
cannot look complete.

Internal section models carry a few extra fields the output is not allowed
to contain (account holder, prescription patient, labeled dates). Those
fields are for local decisions and the audit file.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hospital(S):
    name: str = ""
    registration_number: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    pin: str = ""
    email: str = ""
    phone: str = ""


class Doctor(S):
    name: str = ""
    registration_number: str = ""
    department: str = ""
    designation: str = ""
    qualification: str = ""
    email: str = ""
    phone: str = ""


class Patient(S):
    name: str = ""
    dob: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    pin: str = ""
    tpa_id: str = ""
    employee_id: str = ""
    company_name: str = ""
    pan_card: str = ""
    aadhar_card: str = ""
    phone: str = ""
    email: str = ""


class ClaimInfo(S):
    doa: str = ""
    dod: str = ""
    total_claimed_amount: str = ""


class Bill(S):
    invoice_date: str = ""
    bill_group: str = ""
    bill_category: str = ""
    amount: str = ""
    bill_number: str = ""
    discount: str = ""
    quantity: str = ""
    remarks: str = ""


class Addr(S):
    address: str = ""
    city: str = ""
    state: str = ""
    pinCode: str = ""


class Drug(S):
    name: str = ""
    dosageFrequencyPerDay: int = 0
    dosagefrequencyTotalDays: int = 0
    count: int = 0


class OPD(S):
    reportingComplaint: list[str] = Field(default_factory=list)
    diagnosis: list[str] = Field(default_factory=list)
    investigations: list[str] = Field(default_factory=list)
    pharmacy: list[Drug] = Field(default_factory=list)


class Bank(S):
    bank_name: str = ""
    account_number: str = ""
    bank_address: str = ""
    ifsi_code: str = ""


class Dx(S):
    name: str = ""
    icd: str = ""


class MedicalDetails(S):
    dischargeType: str = ""
    patientAddress: Addr = Field(default_factory=Addr)
    opd_details: OPD = Field(default_factory=OPD)
    Patient_bankinfo: Bank = Field(default_factory=Bank)
    medicalhistory: dict[str, str] = Field(default_factory=dict)
    current_medications: dict[str, str] = Field(default_factory=dict)
    allergies: dict[str, str] = Field(default_factory=dict)
    diagnosis: list[Dx] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    treatment_taken: str = ""


class Room(S):
    room_type: str = ""
    room_category: str = ""
    start_date: str = ""
    end_date: str = ""
    rate: str = ""
    quantity: str = ""
    discount: str = ""
    total_days: str = ""
    total_room_amount: str = ""


class Data(S):
    hospital: Hospital = Field(default_factory=Hospital)
    doctor: Doctor = Field(default_factory=Doctor)
    patient: Patient = Field(default_factory=Patient)
    claim: ClaimInfo = Field(default_factory=ClaimInfo)
    bills: list[Bill] = Field(default_factory=list)
    medicalDetails: MedicalDetails = Field(default_factory=MedicalDetails)
    room_invoice: list[Room] = Field(default_factory=list)


class Claim(S):
    data: Data = Field(default_factory=Data)
    message: str = ""
    success: bool = False


class IdentitySection(S):
    hospital: Hospital = Field(default_factory=Hospital)
    doctor: Doctor = Field(default_factory=Doctor)
    patient: Patient = Field(default_factory=Patient)


class HistorySection(S):
    medicalhistory: dict[str, str] = Field(default_factory=dict)
    current_medications: dict[str, str] = Field(default_factory=dict)
    allergies: dict[str, str] = Field(default_factory=dict)


class OPDSection(S):
    document_patient_name: str = ""
    reportingComplaint: list[str] = Field(default_factory=list)
    diagnosis: list[str] = Field(default_factory=list)
    investigations: list[str] = Field(default_factory=list)
    pharmacy: list[Drug] = Field(default_factory=list)


class BankSection(S):
    account_holder: str = ""
    bank_name: str = ""
    account_number: str = ""
    bank_address: str = ""
    ifsi_code: str = ""


class BillsSection(S):
    bills: list[Bill] = Field(default_factory=list)


class RoomsSection(S):
    room_invoice: list[Room] = Field(default_factory=list)


class VisitSection(S):
    visit_date: str = ""
    admission_date: str = ""
    discharge_date: str = ""
    signature_date: str = ""
    dischargeType: str = ""
    diagnosis: list[Dx] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    treatment_taken: str = ""


class GeneralSection(S):
    """One-shot shape for a PDF that has no assignment page map."""

    hospital: Hospital = Field(default_factory=Hospital)
    doctor: Doctor = Field(default_factory=Doctor)
    patient: Patient = Field(default_factory=Patient)
    visit_date: str = ""
    admission_date: str = ""
    discharge_date: str = ""
    signature_date: str = ""
    bills: list[Bill] = Field(default_factory=list)
    dischargeType: str = ""
    document_patient_name: str = ""
    reportingComplaint: list[str] = Field(default_factory=list)
    opd_diagnosis: list[str] = Field(default_factory=list)
    investigations: list[str] = Field(default_factory=list)
    pharmacy: list[Drug] = Field(default_factory=list)
    account_holder: str = ""
    bank_name: str = ""
    account_number: str = ""
    bank_address: str = ""
    ifsi_code: str = ""
    medicalhistory: dict[str, str] = Field(default_factory=dict)
    current_medications: dict[str, str] = Field(default_factory=dict)
    allergies: dict[str, str] = Field(default_factory=dict)
    diagnosis: list[Dx] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    treatment_taken: str = ""
    room_invoice: list[Room] = Field(default_factory=list)
    total_claimed_amount: str = ""


SECTION_MODELS = {
    "identity": IdentitySection,
    "history": HistorySection,
    "opd": OPDSection,
    "bank": BankSection,
    "bills": BillsSection,
    "room": RoomsSection,
    "visit": VisitSection,
    "record": GeneralSection,
}


def strict_schema(cls: type[BaseModel]) -> dict:
    """JSON schema for Ollama `format`. Every object property is required."""
    schema = cls.model_json_schema()

    def walk(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props)
                if not isinstance(node.get("additionalProperties"), dict):
                    node["additionalProperties"] = False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        Claim.model_validate_json(open(sys.argv[2], encoding="utf-8").read())
        print("schema_valid true")
    else:
        json.dump(strict_schema(Claim), sys.stdout, indent=2)
        sys.stdout.write("\n")
