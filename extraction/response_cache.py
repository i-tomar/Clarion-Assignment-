"""Model-response cache. Keys change when the input, model, prompt, schema, or policy changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_VERSION = "2"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: dict) -> str:
    """Hash of canonical JSON. This fixes key order and whitespace only."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(blob)


def cache_key(material: dict) -> str:
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(blob)


def load_response(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if stored.get("cache_version") != CACHE_VERSION:
        return None
    if stored.get("key") != key:
        return None
    response = stored.get("response")
    if not isinstance(response, str) or not response.strip():
        return None
    return response


def save_response(path: Path, *, key: str, material: dict, response: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "cache_version": CACHE_VERSION,
        "key": key,
        "material": material,
        "response": response,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def count_stale(cache_dir: Path) -> int:
    """Files that this cache will not read. Prototype caches have no cache_version 2."""
    if not cache_dir.is_dir():
        return 0
    stale = 0
    for path in cache_dir.glob("*.json"):
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stale += 1
            continue
        if stored.get("cache_version") != CACHE_VERSION or not stored.get("key"):
            stale += 1
    return stale
