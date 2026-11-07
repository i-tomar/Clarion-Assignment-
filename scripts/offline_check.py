#!/usr/bin/env python3
"""Disconnected-network check for a real extraction run.

Run this yourself after turning the network off. It refuses to run while any
external host is reachable, then runs main.py with fresh OCR (new work
directory) and fresh inference, re-checks the network during and after the
run, and writes `private/offline-test/result-<stamp>.json`. Console output
holds statuses only, never document values.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_HOSTS = ("registry.ollama.ai", "huggingface.co", "pypi.org", "bcebos.com")
EXTERNAL_IPS = ("1.1.1.1", "8.8.8.8", "104.18.16.170")
# Sockets left open by other apps (browser, sync clients, the editor) can stay
# ESTABLISHED for a while after Wi-Fi goes off. Only these processes are the pipeline.
PIPELINE_PROCESSES = {"python.exe", "python3.exe", "ollama.exe", "ollama app.exe", "python", "ollama"}


def external_reachable() -> list[str]:
    reached = []
    for host in EXTERNAL_HOSTS:
        try:
            socket.getaddrinfo(host, 443)
            reached.append(f"dns:{host}")
        except OSError:
            pass
    for address in EXTERNAL_IPS:
        try:
            with socket.create_connection((address, 443), timeout=3):
                reached.append(f"tcp:{address}:443")
        except OSError:
            pass
    return reached


def _process_names() -> dict[str, str]:
    try:
        output = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return {}
    names = {}
    for line in output.splitlines():
        cells = [cell.strip('"') for cell in line.split('","')]
        if len(cells) >= 2:
            names[cells[1]] = cells[0].strip('"')
    return names


def public_connections() -> list[dict]:
    try:
        output = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    names = _process_names()
    found = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "ESTABLISHED":
            continue
        remote = parts[2].rsplit(":", 1)[0].strip("[]")
        try:
            address = ip_address(remote.split("%")[0])
        except ValueError:
            continue
        if address.is_global:
            process = names.get(parts[4], "unknown")
            found.append(
                {
                    "remote": parts[2],
                    "pid": parts[4],
                    "process": process,
                    "pipeline_process": process.lower() in PIPELINE_PROCESSES,
                }
            )
    return found


def _pipeline_sockets(connections: list[dict]) -> list[dict]:
    return [item for item in connections if item["pipeline_process"]]


def loopback_ollama() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
        tags = json.loads(response.read().decode("utf-8"))
    return {
        "reachable": True,
        "models": [model.get("name") for model in tags.get("models") or []],
    }


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result_dir = ROOT / "private" / "offline-test"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / f"result-{stamp}.json"
    result = {"started": datetime.now().isoformat(timespec="seconds"), "offline_verified": False}

    def finish(code: int, note: str) -> int:
        result["finished"] = datetime.now().isoformat(timespec="seconds")
        result["note"] = note
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(note)
        print(f"result file: {result_path}")
        return code

    print("Checking that external hosts are unreachable...")
    result["preflight_external_reachable"] = external_reachable()
    result["preflight_public_connections"] = public_connections()
    if result["preflight_external_reachable"]:
        return finish(3, "NOT OFFLINE: external hosts are reachable. Turn Wi-Fi off and run again. Nothing was run.")
    try:
        result["ollama"] = loopback_ollama()
    except Exception as exc:
        result["ollama"] = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
        return finish(4, "Loopback Ollama is not reachable at 127.0.0.1:11434. Start the Ollama app and run again.")
    if "mistral:7b" not in result["ollama"]["models"]:
        return finish(4, "mistral:7b is not in the local Ollama model list.")

    workdir = ROOT / "private" / f"offline-run-{stamp}"
    command = [
        sys.executable, "main.py", "input/record.pdf",
        "--profile", "clarion-assignment", "--fresh", "--workdir", str(workdir),
    ]
    result["command"] = " ".join(command[1:])
    result["workdir"] = str(workdir)
    print("Offline confirmed. Running fresh OCR and fresh inference on the PDF (about 10 minutes)...")

    probes: list[dict] = []
    stop = threading.Event()

    def monitor():
        while not stop.wait(20):
            probes.append(
                {
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "external_reachable": external_reachable(),
                    "public_connections": public_connections(),
                }
            )

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    started = time.monotonic()
    log_path = result_dir / f"run-{stamp}.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    stop.set()
    watcher.join(timeout=60)
    result["seconds"] = round(time.monotonic() - started, 1)
    result["exit_code"] = process.returncode
    result["run_log"] = str(log_path)
    result["probes_during_run"] = probes
    result["postflight_external_reachable"] = external_reachable()
    result["postflight_public_connections"] = public_connections()

    run_json = workdir / "output" / "run.json"
    audit_json = workdir / "output" / "audit.json"
    if run_json.is_file():
        run = json.loads(run_json.read_text(encoding="utf-8"))
        result["success"] = run.get("success")
        result["inference"] = run.get("inference")
        result["canonical_sha256"] = run.get("canonical_sha256")
        result["model_digest"] = run.get("model_digest")
    if audit_json.is_file():
        audit = json.loads(audit_json.read_text(encoding="utf-8"))
        pages = audit.get("pages") or []
        result["ocr_pages"] = [page["page"] for page in pages if page.get("needs_ocr")]
        result["ocr_errors"] = {str(page["page"]): page["ocr_error"] for page in pages if page.get("ocr_error")}
        result["sections"] = {section["name"]: section["status"] for section in audit.get("sections") or []}
    result["ocr_cache_files_created"] = len(list((workdir / "ocr").glob("page-*.json")))

    reference = ROOT / "private" / "runs" / "fresh-4" / "run.json"
    if reference.is_file():
        online_hash = json.loads(reference.read_text(encoding="utf-8")).get("canonical_sha256")
        result["online_fresh_reference_sha256"] = online_hash
        result["matches_online_fresh_hash"] = online_hash == result.get("canonical_sha256")

    stayed_offline = not any(probe["external_reachable"] for probe in probes) and not result["postflight_external_reachable"]
    snapshots = [result["preflight_public_connections"], result["postflight_public_connections"]]
    snapshots += [probe["public_connections"] for probe in probes]
    pipeline_sockets = [item for snapshot in snapshots for item in _pipeline_sockets(snapshot)]
    other_processes = sorted({item["process"] for snapshot in snapshots for item in snapshot if not item["pipeline_process"]})
    result["stayed_offline_during_run"] = stayed_offline
    result["pipeline_public_sockets_seen"] = pipeline_sockets
    result["other_processes_with_stale_public_sockets"] = other_processes
    result["offline_verified"] = bool(
        stayed_offline
        and not pipeline_sockets
        and process.returncode == 0
        and result.get("success") is True
        and result.get("inference") == "fresh"
        and result["ocr_cache_files_created"] == len(result.get("ocr_pages") or [None])
        and not result.get("ocr_errors")
    )
    if result["offline_verified"]:
        return finish(0, "OFFLINE TEST PASSED. You can turn Wi-Fi back on now.")
    return finish(2, "OFFLINE TEST DID NOT PASS. See the result file. You can turn Wi-Fi back on now.")


if __name__ == "__main__":
    raise SystemExit(main())
