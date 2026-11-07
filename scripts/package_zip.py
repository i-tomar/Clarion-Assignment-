#!/usr/bin/env python3
"""Build a source zip from an allowlist. No PDF, cache, venv, or model weights."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ".gitignore",
    "README.md",
    "APPROACH.md",
    "WORK_LOG.md",
    "TEST_REPORT.md",
    "requirements.txt",
    "requirements-ocr.txt",
    "main.py",
    "extract.py",
    "validate.py",
    "pytest.ini",
    "input/.gitkeep",
    "config/__init__.py",
    "config/settings.py",
    "prompts/__init__.py",
    "prompts/instructions.py",
    "schemas/__init__.py",
    "schemas/medical_schema.py",
    "processors/__init__.py",
    "processors/pdf_processor.py",
    "processors/ocr_processor.py",
    "processors/table_extractor.py",
    "processors/document_classifier.py",
    "extraction/__init__.py",
    "extraction/llm_extractor.py",
    "extraction/identity_resolver.py",
    "extraction/field_mapper.py",
    "extraction/ollama_transport.py",
    "extraction/response_cache.py",
    "validation/__init__.py",
    "validation/schema_validator.py",
    "validation/consistency_checker.py",
    "validation/normalizer.py",
    "examples/make_synthetic_pdf.py",
    "examples/sample-structure.json",
    "scripts/check_env.py",
    "scripts/live_model_check.py",
    "scripts/offline_check.py",
    "scripts/offline_check.py",
    "scripts/run_unit_tests.sh",
    "scripts/package_zip.py",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/test_pipeline.py",
]


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/cursor/artifacts/clarion-local-pipeline.zip")
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        print("missing files:")
        for name in missing:
            print(" ", name)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    print(dest)
    print(f"files={len(FILES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
