#!/usr/bin/env python3
"""Live check of the project's Ollama transport on synthetic text.

Uses the same `extract_section` -> `chat` path as main.py. No medical PDF is
read. No weights are pulled. Exit 0 only when every check passes.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DEFAULT_HOST, DEFAULT_MODEL, generation_options  # noqa: E402
from extraction.llm_extractor import extract_section  # noqa: E402
from extraction.ollama_transport import OllamaError, chat, check_host, fetch_model_digest  # noqa: E402
from prompts.instructions import INSTRUCTIONS  # noqa: E402
from schemas.medical_schema import BillsSection, strict_schema  # noqa: E402

SYNTHETIC = """[PAGE 1]
Bill group: Consultation
Amount: 1200
Bill group: Laboratory
Amount: 850
Subtotal: 2050
"""


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    results = []

    def record(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")

    try:
        digest = fetch_model_digest(DEFAULT_HOST, model)
        record("model digest from local Ollama", digest.startswith("sha256:") or len(digest) == 64, digest)
    except OllamaError as exc:
        record("model digest from local Ollama", False, str(exc))
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        result = extract_section(
            model_cls=BillsSection,
            instruction=INSTRUCTIONS["bills"],
            source=SYNTHETIC,
            model_name=model,
            model_digest=digest,
            options=generation_options(2048),
            policy={"check": "live_model_check"},
            processing={"check": "live_model_check"},
            pdf_sha256="synthetic",
            host=DEFAULT_HOST,
            timeout=600,
            cache_dir=Path(tmp),
            fresh=True,
            write_cache=False,
        )
    ok = result["status"] == "extracted"
    record(
        "schema-constrained extract_section returns valid BillsSection JSON",
        ok,
        f"seconds={result['seconds']} eval_count={result.get('eval_count')} error={result['error'][:200]}",
    )
    if ok:
        amounts = sorted(row["amount"] for row in result["data"]["bills"])
        print(f"     bills returned: {len(result['data']['bills'])}, amounts={amounts}")
        record("schema result contains only synthetic line amounts", set(amounts) <= {"1200", "850"}, str(amounts))

    try:
        chat(
            host=DEFAULT_HOST,
            model="clarion-missing-model:0",
            messages=[{"role": "user", "content": "x"}],
            schema=strict_schema(BillsSection),
            options=generation_options(512),
            timeout=60,
        )
        record("missing model raises OllamaError", False, "no error raised")
    except OllamaError as exc:
        record("missing model raises OllamaError", "404" in str(exc) or "not found" in str(exc), str(exc)[:160])

    try:
        chat(
            host="http://127.0.0.1:9",
            model=model,
            messages=[{"role": "user", "content": "x"}],
            schema={},
            options={},
            timeout=5,
        )
        record("closed loopback port raises OllamaError", False, "no error raised")
    except OllamaError as exc:
        record("closed loopback port raises OllamaError", "not reachable" in str(exc), str(exc)[:160])

    try:
        check_host("http://192.0.2.1:11434")
        record("non-loopback host refused", False, "accepted")
    except OllamaError as exc:
        record("non-loopback host refused", True, str(exc)[:120])

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
