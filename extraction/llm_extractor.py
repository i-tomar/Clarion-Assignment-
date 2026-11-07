"""One schema-constrained Ollama call, with a response cache.

The cache stores the model text. It is not a substitute for a fresh call.
Keys include the model name and digest, prompt, schema, options, policy,
processing config, and source hash.
"""

from __future__ import annotations

import json
from time import monotonic

from pydantic import ValidationError

from config.settings import PIPELINE_VERSION, generation_options
from extraction.ollama_transport import OllamaError, chat
from extraction.response_cache import cache_key, load_response, save_response, sha256_text
from prompts.instructions import RULES
from schemas.medical_schema import strict_schema

__all__ = ["OllamaError", "chat", "extract_section", "generation_options"]


def extract_section(
    *,
    model_cls,
    instruction: str,
    source: str,
    model_name: str,
    model_digest: str,
    options: dict,
    policy: dict,
    processing: dict,
    pdf_sha256: str,
    host: str,
    timeout: int,
    cache_dir,
    fresh: bool,
    write_cache: bool,
    chat_func=None,
):
    schema = strict_schema(model_cls)
    material = {
        "cache_version": "2",
        "pipeline_version": PIPELINE_VERSION,
        "model": model_name,
        "model_digest": model_digest,
        "options": options,
        "policy": policy,
        "processing": processing,
        "pdf_sha256": pdf_sha256,
        "prompt_sha256": sha256_text(RULES + "\n" + instruction),
        "schema_sha256": sha256_text(json.dumps(schema, sort_keys=True, separators=(",", ":"))),
        "source_sha256": sha256_text(source),
    }
    key = cache_key(material)
    path = cache_dir / f"{key}.json"
    caller = chat_func or _live_chat
    if not fresh:
        cached = load_response(path, key)
        if cached is not None:
            try:
                obj = model_cls.model_validate_json(cached)
            except ValidationError:
                cached = None
            else:
                return {
                    "status": "extracted",
                    "cache": "hit",
                    "data": obj.model_dump(mode="json"),
                    "error": "",
                    "seconds": 0.0,
                    "key": key,
                    "model_digest": model_digest,
                }
    messages = [
        {"role": "system", "content": RULES},
        {"role": "user", "content": instruction + "\n\nSource:\n" + source},
    ]
    started = monotonic()
    error = ""
    content = ""
    for attempt in range(2):
        try:
            response = caller(
                host=host,
                model=model_name,
                messages=messages,
                schema=schema,
                options=options,
                timeout=timeout,
            )
            content = response["content"]
            obj = model_cls.model_validate_json(content)
        except OllamaError as exc:
            error = str(exc)
            break
        except ValidationError as exc:
            error = str(exc)[:700]
            if attempt == 0 and content:
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": "The JSON failed validation. Return only a corrected JSON object. Do not invent values. " + error,
                    }
                )
                continue
            break
        else:
            if write_cache:
                save_response(path, key=key, material=material, response=content)
            return {
                "status": "extracted",
                "cache": "bypass" if fresh else "miss",
                "data": obj.model_dump(mode="json"),
                "raw": content,
                "error": "",
                "seconds": round(monotonic() - started, 3),
                "key": key,
                "model_digest": model_digest,
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
            }
    return {
        "status": "failed",
        "cache": "bypass" if fresh else "miss",
        "data": None,
        "error": error or "model call failed",
        "seconds": round(monotonic() - started, 3),
        "key": key,
        "model_digest": model_digest,
    }


def _live_chat(**kwargs):
    return chat(**kwargs)
