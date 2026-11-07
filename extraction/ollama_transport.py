"""Local Ollama chat. Loopback HTTP only. No hosted fallback."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse


class OllamaError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaError(f"refusing HTTP redirect to {newurl}")


def check_host(host: str) -> str:
    raw = (host or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.scheme != "http":
        raise OllamaError("Only http to a loopback Ollama is allowed.")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise OllamaError(
            f"Refusing Ollama host {parsed.hostname!r}. This tool does not call hosted APIs."
        )
    if parsed.username or parsed.password:
        raise OllamaError("Refusing credentials embedded in the Ollama URL.")
    port = parsed.port or 11434
    return f"http://{parsed.hostname}:{port}"


def chat(
    *,
    host: str,
    model: str,
    messages: list[dict],
    schema: dict,
    options: dict,
    timeout: int,
    keep_alive: int = 0,
) -> dict:
    url = check_host(host).rstrip("/") + "/api/chat"
    body = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "options": options,
        "keep_alive": keep_alive,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OllamaError:
        raise
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise OllamaError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Ollama is not reachable at {check_host(host)} ({exc.reason}). "
            "Start it on loopback. No hosted fallback is configured."
        ) from exc
    except TimeoutError as exc:
        raise OllamaError(f"Ollama request timed out after {timeout}s") from exc
    message = payload.get("message") or {}
    content = message.get("content", "")
    if isinstance(content, dict):
        content = json.dumps(content)
    if not isinstance(content, str) or not content.strip():
        raise OllamaError("Ollama returned an empty message")
    return {
        "content": content,
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
        "done_reason": payload.get("done_reason"),
    }


def fetch_model_digest(host: str, model: str, timeout: int = 30) -> str:
    """Local model id from Ollama. No weight download.

    `/api/show` confirms the model is installed locally. Ollama 0.40 does not
    put a digest in that response, so the manifest digest comes from the
    matching `/api/tags` entry.
    """
    base = check_host(host).rstrip("/")
    payload = _local_json(base + "/api/show", {"name": model}, timeout, "reading model details")
    digest = payload.get("digest") or ""
    if isinstance(digest, str) and digest.strip():
        return digest.strip()
    tags = _local_json(base + "/api/tags", None, timeout, "listing local models")
    wanted = _with_tag(model)
    for entry in tags.get("models") or []:
        names = {_with_tag(str(entry.get(key) or "")) for key in ("name", "model")}
        if wanted in names:
            digest = entry.get("digest") or ""
            if isinstance(digest, str) and digest.strip():
                return digest.strip()
    raise OllamaError(f"Ollama did not return a digest for model {model!r}")


def _with_tag(name: str) -> str:
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def _local_json(url: str, body: dict | None, timeout: int, action: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except OllamaError:
        raise
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise OllamaError(f"Ollama HTTP {exc.code} while {action}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Ollama is not reachable at {url.rsplit('/api/', 1)[0]} ({exc.reason}). "
            "Start it on loopback. No hosted fallback is configured."
        ) from exc
    except TimeoutError as exc:
        raise OllamaError(f"Ollama timed out after {timeout}s while {action}") from exc
