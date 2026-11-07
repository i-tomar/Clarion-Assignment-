#!/usr/bin/env python3
"""Print schema validation and factual warnings as separate sections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from validation.consistency_checker import check_consistency
from validation.schema_validator import validate_schema


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a claim JSON. Schema and factual checks are separate.")
    parser.add_argument("json_path", nargs="?", default="output/output.json")
    parser.add_argument("--audit", default=None)
    parser.add_argument("--reference", default=None, help="Optional manual reference. Differences are not a model score.")
    args = parser.parse_args(argv)
    path = Path(args.json_path)
    if not path.is_file():
        print(json.dumps({"schema_valid": False, "errors": [f"file not found: {path}"]}, indent=2))
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = validate_schema(payload)
    report = {"schema": {"schema_valid": schema["schema_valid"], "errors": schema["errors"]}}
    if not schema["schema_valid"]:
        print(json.dumps(report, indent=2))
        return 1
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8")) if args.audit else None
    report["factual"] = check_consistency(schema["claim"], audit)
    if args.reference:
        reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        report["reference_comparison"] = {
            "label": "reference-only",
            "note": (
                "A reference file is a manual review. It is not pipeline output and a mismatch is not an accuracy score. "
                "Known manual references can contain inferred dates, merged prescriptions, or linked bank details."
            ),
            "mismatches": _mismatches(schema["claim"], reference),
        }
    print(json.dumps(report, indent=2))
    return 0


def _mismatches(got, reference):
    flat_got = dict(_flatten(got))
    flat_ref = dict(_flatten(reference))
    rows = []
    for key in sorted(set(flat_got) | set(flat_ref)):
        if flat_got.get(key) != flat_ref.get(key):
            rows.append({"field": key, "actual": flat_got.get(key), "reference": flat_ref.get(key)})
    return rows


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            yield from _flatten(item, path)
    elif isinstance(value, list):
        if not value:
            yield prefix, []
        for index, item in enumerate(value):
            yield from _flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


if __name__ == "__main__":
    raise SystemExit(main())
