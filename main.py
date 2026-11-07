#!/usr/bin/env python3
"""Local medical-PDF extraction. Loopback Ollama only. No hosted fallback."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import (
    DEFAULT_DPI,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    PIPELINE_VERSION,
    allowed_keys,
    force_ocr_pages,
    generation_options,
    section_plan,
    select_pages,
)
from extraction.field_mapper import apply_policies, policy_snapshot
from extraction.llm_extractor import extract_section
from extraction.ollama_transport import OllamaError, check_host, fetch_model_digest
from extraction.response_cache import canonical_hash, count_stale, sha256_file
from processors.document_classifier import classify
from processors.ocr_processor import (
    OCR_CACHE_VERSION,
    PREPROCESS_REVISION,
    ocr_cache_usable,
    prepare_image,
    recognize,
)
from processors.pdf_processor import Document, PdfError, backend_name
from processors.table_extractor import tables_from_ocr_lines, tables_from_pdf
from prompts.instructions import INSTRUCTIONS
from schemas.medical_schema import Claim, SECTION_MODELS

ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Extract a medical PDF into the assignment JSON locally.")
    parser.add_argument("pdf", nargs="?", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--backend", choices=["paddle", "rapidocr", "none"], default="paddle")
    parser.add_argument("--ocr-only", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Bypass the model response cache. Same as --fresh.")
    parser.add_argument("--fresh", action="store_true", help="Bypass the model response cache.")
    parser.add_argument("--repeatability", action="store_true", help="Run fresh inference more than once and print canonical hashes.")
    parser.add_argument("--repeatability-runs", type=int, default=2)
    parser.add_argument("--pages", default=None, help="Page spec such as 6-12 or all. Overrides the profile range.")
    parser.add_argument("--profile", choices=["generic", "clarion-assignment"], default="generic")
    parser.add_argument("--template-mode", choices=["auto", "exclude", "keep"], default="auto")
    parser.add_argument("--bank-policy", choices=["link_if_name_match", "always", "never"], default="link_if_name_match")
    parser.add_argument("--associate-bank", action="store_true", help="Copy extracted bank fields even when the holder is unverified.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--workdir", default=None)
    return parser.parse_args(argv)


def main(argv=None, *, chat_func=None, digest_func=None) -> int:
    args = parse_args(argv)
    if args.skip_ocr and args.ocr_only:
        print("Choose only one of --skip-ocr and --ocr-only.", file=sys.stderr)
        return 1
    if args.repeatability and args.repeatability_runs < 2:
        print("--repeatability-runs must be at least 2.", file=sys.stderr)
        return 1
    try:
        host = check_host(args.host)
    except OllamaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    pdf_path = _pdf_path(args)
    if pdf_path is None:
        print("Pass a PDF path, or copy a file to input/record.pdf. The sample PDF is not included.", file=sys.stderr)
        return 1
    work = Path(args.workdir).resolve() if args.workdir else ROOT
    try:
        if args.repeatability:
            return _repeat(args, pdf_path, work, host, chat_func=chat_func, digest_func=digest_func)
        return _execute(args, pdf_path, work, host, chat_func=chat_func, digest_func=digest_func, write_output=True).exit_code
    except PdfError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _repeat(args, pdf_path, work, host, *, chat_func, digest_func) -> int:
    hashes = []
    claims = []
    last = None
    run_dir = work / "output" / "repeatability"
    for index in range(args.repeatability_runs):
        last = _execute(
            args,
            pdf_path,
            work,
            host,
            chat_func=chat_func,
            digest_func=digest_func,
            write_output=index == args.repeatability_runs - 1,
            fresh=True,
            write_cache=False,
        )
        claims.append(last.claim)
        if last.claim is not None:
            _dump(run_dir / f"run-{index + 1}.json", last.claim)
        hashes.append(
            {
                "run": index + 1,
                "canonical_sha256": last.canonical,
                "success": last.success,
                "message": last.message,
                "inference": "fresh",
                "response_cache": "not_read_not_written",
            }
        )
    report = {
        "label": "fresh_repeatability_mocked_or_live",
        "note": (
            "Each run bypassed the extraction response cache. "
            "Equal hashes mean these runs matched after canonical JSON formatting. "
            "temperature 0 and seed do not guarantee the same tokens on other hardware."
        ),
        "runs": hashes,
        "hashes_equal": len({item["canonical_sha256"] for item in hashes}) == 1,
        "differing_fields": _differing_fields(claims),
        "per_run_claims": str(run_dir),
    }
    dest = work / "output" / "repeatability.json"
    _dump(dest, report)
    print(json.dumps(report, indent=2))
    return last.exit_code


def _differing_fields(claims) -> list[str]:
    """Field paths whose values are not identical across runs. Values are not listed."""
    flats = [dict(_flatten(claim)) for claim in claims if claim is not None]
    if len(flats) < 2:
        return []
    paths = sorted(set().union(*flats))
    return [path for path in paths if len({json.dumps(flat.get(path), sort_keys=True) for flat in flats}) > 1]


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten(item, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


class _Outcome:
    def __init__(self, exit_code, canonical, success, message, claim=None):
        self.exit_code = exit_code
        self.canonical = canonical
        self.success = success
        self.message = message
        self.claim = claim


def _execute(args, pdf_path, work, host, *, chat_func, digest_func, write_output, fresh=None, write_cache=None) -> _Outcome:
    fresh = bool(args.fresh or args.refresh or args.repeatability) if fresh is None else fresh
    if write_cache is None:
        write_cache = not args.repeatability
    bank_policy = "always" if args.associate_bank else args.bank_policy
    out_dir = work / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_sha = sha256_file(pdf_path)
    document = Document(pdf_path)
    try:
        selected = select_pages(args.profile, args.pages, document.page_count)
        if not selected:
            message = (
                f"No pages left in range for profile {args.profile}. "
                "The assignment profile uses pages 6-12 only when the PDF has those pages. "
                "Pass --pages explicitly for a shorter PDF."
            )
            _log(out_dir, message)
            if write_output:
                _write_failure(out_dir, args, message, pdf_sha, smoke=args.smoke)
            return _Outcome(2, None, False, message)
        records = _load_pages(document, selected, args, work, pdf_sha)
    finally:
        document.close()

    excluded = set()
    if args.template_mode != "keep":
        excluded = {number for number, record in records.items() if record["template"]}
    for number in excluded:
        records[number]["excluded_as_template"] = True
        records[number]["visible"] = ""
    if excluded:
        _log(out_dir, f"Excluded instruction/template pages: {sorted(excluded)}")

    if args.ocr_only:
        failed = [number for number, record in records.items() if record["needs_ocr"] and record["ocr_error"]]
        status = {"pdf_sha256": pdf_sha, "pages": _page_audit(records), "failed_ocr_pages": failed}
        _dump(out_dir / "ocr_status.json", status)
        if failed:
            print(f"OCR failed on pages {failed}. success is not set. No claim JSON was written.")
            return _Outcome(2, None, False, "ocr failed")
        print(f"OCR status written to {out_dir / 'ocr_status.json'}")
        return _Outcome(0, None, False, "ocr only")

    digest = ""
    digest_error = ""
    try:
        if digest_func is not None:
            digest = digest_func(host, args.model)
        else:
            digest = fetch_model_digest(host, args.model, timeout=min(args.timeout, 30))
    except OllamaError as exc:
        digest_error = str(exc)

    plan = section_plan(args.profile, [number for number in selected if number not in excluded], smoke=args.smoke)
    if not plan:
        message = "No sections left after template exclusion and the page range."
        if write_output:
            _write_failure(out_dir, args, message, pdf_sha, smoke=args.smoke, digest_error=digest_error)
        return _Outcome(2, None, False, message)

    processing = {
        "profile": args.profile,
        "pages": selected,
        "template_mode": args.template_mode,
        "excluded_template_pages": sorted(excluded),
        "dpi": args.dpi,
        "ocr_backend": args.backend,
        "preprocess_revision": PREPROCESS_REVISION,
        "pdf_backend": backend_name(),
        "pipeline_version": PIPELINE_VERSION,
    }
    policy = policy_snapshot(bank_policy)
    sections = []
    bundle = _empty_bundle(excluded, records)
    if digest_error:
        for spec in plan:
            sections.append(
                {
                    "name": spec.name,
                    "status": "failed",
                    "cache": "not_used",
                    "error": digest_error,
                    "pages": list(spec.pages),
                }
            )
        _log(out_dir, digest_error)
    else:
        cache_dir = out_dir / "cache"
        for spec in plan:
            source = _section_source(records, spec.pages, excluded)
            if not source.strip():
                sections.append(
                    {
                        "name": spec.name,
                        "status": "failed_no_source",
                        "cache": "not_used",
                        "error": "no usable text for this section (OCR missing, page excluded, or page empty)",
                        "pages": list(spec.pages),
                    }
                )
                continue
            result = extract_section(
                model_cls=SECTION_MODELS[spec.model_name],
                instruction=INSTRUCTIONS[spec.model_name],
                source=source,
                model_name=args.model,
                model_digest=digest,
                options=generation_options(args.num_ctx),
                policy=policy,
                processing=processing,
                pdf_sha256=pdf_sha,
                host=host,
                timeout=args.timeout,
                cache_dir=cache_dir,
                fresh=fresh,
                write_cache=write_cache,
                chat_func=chat_func,
            )
            result["name"] = spec.name
            result["pages"] = list(spec.pages)
            sections.append(result)
            _log(out_dir, f"{spec.name}: {result['status']} cache={result['cache']}")
            if result["status"] == "extracted":
                _merge(bundle, spec.name, result["data"], spec.pages)
                raw_path = out_dir / f"{spec.name}-raw.txt"
                if result.get("raw"):
                    raw_path.write_text(result["raw"], encoding="utf-8")

    ok = {section["name"] for section in sections if section["status"] == "extracted"}
    if args.profile == "clarion-assignment":
        allow_money = "bills" in ok and "room" in ok
    else:
        allow_money = "record" in ok
    data, fragment = apply_policies(bundle, _visible_map(records, excluded), bank_policy=bank_policy, allow_money=allow_money, observation_pages=_text_map(records))
    failed = [section for section in sections if str(section["status"]).startswith("failed")]
    ocr_failed = [
        number
        for number, record in records.items()
        if number not in excluded and record["needs_ocr"] and not record.get("ocr_text")
    ]
    if args.smoke and not failed and not ocr_failed:
        success = False
        message = "Partial: smoke test only"
    elif failed or ocr_failed:
        parts = []
        if failed:
            details = []
            for section in failed:
                detail = str(section.get("error") or section["status"]).replace("\n", " ")[:240]
                details.append(f"{section['name']} ({detail})")
            parts.append("failed sections " + "; ".join(details))
        if ocr_failed:
            parts.append("OCR incomplete on pages " + ", ".join(str(number) for number in ocr_failed))
        message = "Partial: " + "; ".join(parts)
        if args.smoke:
            message = "Partial: smoke test only; " + message
        success = False
    elif not sections:
        success = False
        message = "No sections ran"
    else:
        success = True
        message = "Success"
    claim = Claim.model_validate({"data": data, "message": message, "success": success}).model_dump(mode="json")
    canonical = canonical_hash(claim)
    caches = [section.get("cache") for section in sections if section.get("status") == "extracted"]
    if fresh:
        inference = "fresh"
    elif caches and all(item == "hit" for item in caches):
        inference = "cached"
    elif any(item == "hit" for item in caches):
        inference = "mixed"
    elif caches:
        inference = "fresh"
    else:
        inference = "not_run"
    audit = {
        "pipeline_version": PIPELINE_VERSION,
        "model": args.model,
        "model_digest": digest,
        "inference": inference,
        "response_cache_bypassed": fresh,
        "canonical_sha256": canonical,
        "pdf_sha256": pdf_sha,
        "pdf_backend": backend_name(),
        "excluded_template_pages": sorted(excluded),
        "stale_cache_files_ignored": count_stale(out_dir / "cache"),
        "sections": [
            {key: value for key, value in section.items() if key not in {"data", "raw"}}
            for section in sections
        ],
        "pages": _page_audit(records),
        **fragment,
    }
    if write_output:
        target = out_dir / ("smoke.json" if args.smoke else "output.json")
        audit_path = out_dir / ("smoke_audit.json" if args.smoke else "audit.json")
        run_path = out_dir / ("smoke_run.json" if args.smoke else "run.json")
        _dump(target, claim)
        _dump(audit_path, audit)
        _dump(
            run_path,
            {
                "pipeline_version": PIPELINE_VERSION,
                "inference": inference,
                "canonical_sha256": canonical,
                "file": str(target),
                "success": success,
                "message": message,
                "model": args.model,
                "model_digest": digest,
                "response_cache_bypassed": fresh,
            },
        )
        print(f"success={str(success).lower()}")
        print(f"inference={inference}")
        print(f"canonical_sha256={canonical}")
        print(f"output={target}")
        print(f"audit={audit_path}")
        print("Canonical hash fixes JSON key order and whitespace only. It is not a hardware-independent inference guarantee.")
    return _Outcome(0 if success else 2, canonical, success, message, claim)


def _load_pages(document: Document, selected, args, work: Path, pdf_sha: str) -> dict:
    forced = force_ocr_pages(args.profile)
    records = {}
    for number in selected:
        text = document.text(number)
        info = classify(text, force_ocr=number in forced)
        record = {
            "number": number,
            "text_layer": text,
            "template": info["template"],
            "role": info["role"],
            "kind": info["kind"],
            "letters": info["letters"],
            "needs_ocr": info["needs_ocr"],
            "ocr_text": "",
            "ocr_error": "",
            "lines": [],
            "table_text": "",
            "method": "text-layer",
            "excluded_as_template": False,
            "inverted": False,
        }
        if info["needs_ocr"]:
            cached = _read_ocr_cache(work, pdf_sha, number, args)
            if cached:
                record.update(cached)
            elif args.skip_ocr:
                record["ocr_error"] = "OCR skipped and no usable local OCR cache for this page"
            elif args.backend == "none":
                record["ocr_error"] = "OCR backend is none and this page has no usable text layer"
            else:
                image = document.render(number, work / "pages", args.dpi)
                prepared = work / "pages" / f"processed-{number:02d}.png"
                note = prepare_image(image, prepared)
                record["inverted"] = bool(note.get("inverted"))
                ocr_image = Path(note["image"]) if note.get("image") else image
                recognized = recognize(ocr_image, args.backend)
                record["ocr_text"] = recognized.get("text") or ""
                record["lines"] = recognized.get("lines") or []
                record["ocr_error"] = recognized.get("error") or ""
                record["ocr_backend"] = recognized.get("backend") or args.backend
                _write_ocr_cache(work, pdf_sha, number, args, record)
            if record["ocr_text"]:
                record["table_text"] = tables_from_ocr_lines(record["lines"])
                record["method"] = "ocr+text-layer" if text else "ocr"
        else:
            record["table_text"] = tables_from_pdf(document.path, number)
        record["visible"] = _visible(record)
        records[number] = record
    return records


def _visible(record: dict) -> str:
    if record["needs_ocr"] and not record["ocr_text"]:
        return ""
    parts = []
    if record["text_layer"] and not record["needs_ocr"]:
        parts.append(record["text_layer"])
    elif record["text_layer"] and record["ocr_text"]:
        parts.append(record["text_layer"])
        parts.append("[IMAGE OCR]\n" + record["ocr_text"])
    elif record["ocr_text"]:
        parts.append("[IMAGE OCR]\n" + record["ocr_text"])
    if record["table_text"]:
        parts.append("[TABLE]\n" + record["table_text"])
    return "\n\n".join(part for part in parts if part.strip())


def _section_source(records, pages, excluded) -> str:
    chunks = []
    for number in pages:
        if number in excluded:
            continue
        body = (records.get(number) or {}).get("visible", "").strip()
        if body:
            chunks.append(f"[PAGE {number}]\n{body}")
    return "\n\n".join(chunks)


def _visible_map(records, excluded) -> dict:
    return {
        number: record["visible"]
        for number, record in records.items()
        if number not in excluded and record.get("visible")
    }


def _text_map(records) -> dict:
    mapped = {}
    for number, record in records.items():
        if record.get("excluded_as_template"):
            continue
        text = record.get("visible") or record.get("text_layer") or ""
        if text:
            mapped[number] = text
    return mapped


def _empty_bundle(excluded, records) -> dict:
    page_meta = {
        number: {"method": record["method"], "lines": record.get("lines") or [], "role": record["role"]}
        for number, record in records.items()
        if number not in excluded
    }
    return {
        "hospital": {},
        "doctor": {},
        "patient": {},
        "admission_date": "",
        "discharge_date": "",
        "visit_date": "",
        "signature_date": "",
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
        "template_pages": set(excluded),
        "page_meta": page_meta,
    }


def _merge(bundle, name, data, pages):
    if name == "identity":
        bundle["hospital"] = data.get("hospital") or {}
        bundle["doctor"] = data.get("doctor") or {}
        bundle["patient"] = data.get("patient") or {}
    elif name == "history":
        bundle["medicalhistory"] = data.get("medicalhistory") or {}
        bundle["current_medications"] = data.get("current_medications") or {}
        bundle["allergies"] = data.get("allergies") or {}
    elif name == "opd":
        bundle["rx_patient_name"] = data.get("document_patient_name") or ""
        bundle["reportingComplaint"] = data.get("reportingComplaint") or []
        bundle["opd_diagnosis"] = data.get("diagnosis") or []
        bundle["investigations"] = data.get("investigations") or []
        bundle["pharmacy"] = data.get("pharmacy") or []
    elif name == "bank":
        for key in ("account_holder", "bank_name", "account_number", "bank_address", "ifsi_code"):
            bundle[key] = data.get(key) or ""
    elif name == "bills":
        bundle["bills"] = data.get("bills") or []
    elif name == "room":
        bundle["room_invoice"] = data.get("room_invoice") or []
    elif name == "visit":
        for key in ("visit_date", "admission_date", "discharge_date", "signature_date", "dischargeType", "diagnosis", "symptoms", "treatment_taken"):
            bundle[key] = data.get(key) or ([] if key in {"diagnosis", "symptoms"} else "")
    elif name == "record":
        for key in (
            "hospital", "doctor", "patient", "visit_date", "admission_date", "discharge_date", "signature_date",
            "bills", "dischargeType", "reportingComplaint", "opd_diagnosis", "investigations", "pharmacy",
            "account_holder", "bank_name", "account_number", "bank_address", "ifsi_code", "medicalhistory",
            "current_medications", "allergies", "diagnosis", "symptoms", "treatment_taken", "room_invoice",
        ):
            if key in data:
                bundle[key] = data.get(key) or bundle[key]
        bundle["rx_patient_name"] = data.get("document_patient_name") or ""
        bundle["model_total"] = data.get("total_claimed_amount") or ""
    for key in allowed_keys(name):
        bundle["allowed"].setdefault(key, set()).update(pages)


def _read_ocr_cache(work: Path, pdf_sha: str, number: int, args) -> dict | None:
    path = work / "ocr" / f"page-{number:02d}.json"
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not ocr_cache_usable(stored, pdf_sha256=pdf_sha, page=number, backend=args.backend, dpi=args.dpi):
        return None
    return {
        "ocr_text": stored.get("text") or "",
        "lines": stored.get("lines") or [],
        "ocr_error": "",
        "inverted": bool(stored.get("inverted")),
    }


def _write_ocr_cache(work: Path, pdf_sha: str, number: int, args, record: dict) -> None:
    if record.get("ocr_error"):
        return
    path = work / "ocr" / f"page-{number:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(
        path,
        {
            "cache_version": OCR_CACHE_VERSION,
            "pdf_sha256": pdf_sha,
            "page": number,
            "backend": args.backend,
            "dpi": args.dpi,
            "preprocess_revision": PREPROCESS_REVISION,
            "text": record.get("ocr_text") or "",
            "lines": record.get("lines") or [],
            "inverted": record.get("inverted"),
            "error": "",
        },
    )


def _page_audit(records: dict) -> list[dict]:
    rows = []
    for number in sorted(records):
        record = records[number]
        rows.append(
            {
                "page": number,
                "role": record["role"],
                "kind": record["kind"],
                "template": record["template"],
                "excluded_as_template": record["excluded_as_template"],
                "needs_ocr": record["needs_ocr"],
                "method": record["method"],
                "letters": record["letters"],
                "ocr_error": record["ocr_error"],
                "ocr_line_count": len(record.get("lines") or []),
                "inverted": record.get("inverted"),
            }
        )
    return rows


def _write_failure(out_dir: Path, args, message: str, pdf_sha: str, *, smoke: bool, digest_error: str = "") -> None:
    claim = Claim.model_validate({"data": {}, "message": message, "success": False}).model_dump(mode="json")
    target = out_dir / ("smoke.json" if smoke else "output.json")
    _dump(target, claim)
    _dump(
        out_dir / ("smoke_audit.json" if smoke else "audit.json"),
        {
            "pipeline_version": PIPELINE_VERSION,
            "inference": "not_run",
            "pdf_sha256": pdf_sha,
            "error": digest_error or message,
            "sections": [],
            "evidence": [],
            "warnings": [{"code": "not_run", "message": message}],
        },
    )


def _pdf_path(args) -> Path | None:
    if args.pdf:
        return Path(args.pdf).resolve()
    default = ROOT / "input" / "record.pdf"
    if default.is_file():
        return default
    return None


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _log(out_dir: Path, message: str) -> None:
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    line = f"{stamp} {message}"
    print(line)
    with (out_dir / "run_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
