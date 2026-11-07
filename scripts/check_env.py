#!/usr/bin/env python3
"""Report the local toolchain. Does not download models or open a medical PDF."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    print(f"python={sys.version.split()[0]}")
    print(f"platform={platform.platform()}")
    print(f"cpu_count={os_cpu()}")
    for command in ("pdfinfo", "pdftotext", "pdftoppm", "ollama"):
        print(f"{command}={shutil.which(command) or 'MISSING'}")
    for module in ("pydantic", "fitz", "pdfplumber", "paddleocr"):
        spec = importlib.util.find_spec(module)
        print(f"import_{module}={'yes' if spec else 'no'}")
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            body = response.read(400).decode("utf-8", errors="replace")
        print("ollama_loopback=reachable")
        print("ollama_tags_prefix=" + body[:180].replace("\n", " "))
    except Exception as exc:
        print(f"ollama_loopback=not_reachable ({type(exc).__name__}: {exc})")
    weights = Path.home() / ".paddlex" / "official_models"
    cached = sorted(path.name for path in weights.iterdir() if path.is_dir()) if weights.is_dir() else []
    print(f"paddle_ocr_weights={','.join(cached) or 'none cached'}")
    print("This check does not pull weights and does not read a medical PDF.")
    print("READY for unit tests if python, pydantic, fitz, and pdfplumber import.")
    print("NOT READY for live extraction until Ollama is up and the chosen model is pulled.")
    return 0


def os_cpu() -> str:
    try:
        import os
        return str(os.cpu_count())
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
