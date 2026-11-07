# Local medical PDF extraction

A local pipeline that turns a PDF into one JSON object with the assignment's key names. Inference and OCR run on this machine only. The model call goes to `http://127.0.0.1:11434`; other hosts are refused. There is no hosted model, hosted OCR, account, or upload step in the code.

This package does not include a medical PDF, a filled extraction, or model weights. `examples/sample-structure.json` is an empty schema sample. It is not an extraction result.

## Environment this revision was run on

Observed on 7 Nov 2026 on the target laptop. These are measurements, not minimum requirements.

| Item | Observed |
| --- | --- |
| OS | Windows 11 Home Single Language 10.0.26200, 64-bit |
| CPU | Intel Core i5-12450H, 8 cores / 12 threads. Intel UHD integrated graphics only; Ollama dropped the iGPU and ran on CPU. |
| Memory | 15.7 GB total. Free memory varied from 1.9 GB to 7.9 GB during the session depending on other apps (WSL, Edge). |
| Disk | C: 88.8 GB free before setup. Model weights are on C: (`%USERPROFILE%\.ollama\models`). |
| Python | CPython 3.13.9 (the only Python installed). 3.12 was the earlier build machine; 3.13 is what ran here. |
| Packages | pydantic 2.14.3, PyMuPDF 1.26.7, pdfplumber 0.11.10, pytest 9.2.0, tzdata 2026.6, paddleocr 3.7.0, paddlepaddle 3.3.1 (cp313 win_amd64 wheel), paddlex 3.7.2, numpy 2.3.5, opencv-contrib-python 4.10.0.84, Pillow 13.0.0 |
| Ollama | 0.40.15, tray app, one server on `127.0.0.1:11434`, `OLLAMA_NO_CLOUD=1` (server log: "Ollama cloud disabled: true") |
| Model | `mistral:7b`, digest `6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf`, llama architecture, 7.2B parameters, GGUF Q4_K_M, 4,372,824,384 bytes |
| OCR weights | `PP-OCRv4_mobile_det` (4.7 MB) and `en_PP-OCRv4_mobile_rec` (7.5 MB) in `%USERPROFILE%\.paddlex\official_models` |
| Poppler | Not installed (a `pdftotext` from Git for Windows is on PATH). PyMuPDF was used, so the Poppler fallback did not run. |

## Install (Windows, as executed)

```powershell
cd d:\clarion-local-pipeline
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

`requirements.txt` now includes `tzdata` on Windows. Without it `main.py` fails at import with `ZoneInfoNotFoundError: Asia/Kolkata`, because Windows has no system time-zone database.

Linux and macOS use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`. They were not run for this revision.

## Ollama (one-time, local only)

Ollama is a separate application, not a pip requirement. No account is needed.

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD','1','User')   # restart the Ollama app afterwards
ollama --version
ollama list
ollama pull mistral:7b      # one-time, needs the network; ~4.4 GB
ollama show mistral:7b
```

Leave `OLLAMA_HOST` unset (default `127.0.0.1:11434`). Never bind it to `0.0.0.0`. The Windows tray app starts exactly one server; do not also run `ollama serve` in a terminal. To check: `netstat -ano | findstr :11434` should show one `LISTENING` line on `127.0.0.1`.

`ollama pull` used the normal registry route and completed on this laptop. Nothing was bypassed.

Live transport check on synthetic text (same code path as `main.py`):

```powershell
.\.venv\Scripts\python.exe scripts\live_model_check.py
```

It checks the model digest, one schema-constrained call, a missing model (HTTP 404), a closed loopback port, and a refused non-loopback host.

## OCR weights (one-time)

The first OCR call downloads the two PaddleOCR models above into `%USERPROFILE%\.paddlex`. That happened during setup with a synthetic image. Later runs, including the offline run, loaded them from disk. OCR runs on CPU (`device="cpu"`, `enable_mkldnn=False`, 2 threads).

## Run

Put the PDF at `input\record.pdf` (gitignored) or pass a path.

```powershell
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --fresh
```

`--profile clarion-assignment` selects pages 6–12 when the PDF has them, so the instruction and example pages 1–5 are never read. It is not a universal skip of pages 1–5 for other PDFs; a short PDF stops with `success: false`.

To keep OCR and Mistral out of memory at the same time on a busy laptop, run OCR first, then extraction (the second step reuses the local OCR cache):

```powershell
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --ocr-only
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --fresh
```

Other commands:

```powershell
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --repeatability --repeatability-runs 5
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment      # cached rerun
.\.venv\Scripts\python.exe validate.py output/output.json --audit output/audit.json
.\.venv\Scripts\python.exe scripts\check_env.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

`extract.py` and `main.py` are the same CLI. On this laptop one fresh run takes about 8–9 minutes (about 1 minute OCR, the rest is seven CPU model calls). A cached run takes about 1.5 seconds and makes no model call.

`--fresh` and `--refresh` do not read the response cache. `--repeatability` runs the fresh path several times, does not read or write the response cache, writes `output/repeatability.json`, stores each run's claim under `output/repeatability/`, and lists differing field paths (never values). Equal canonical hashes only mean those JSON documents matched after key sorting. Temperature 0 and seed 42 do not guarantee identical completions on other hardware, other Ollama versions, or other settings.

## What you get

| File | Meaning |
| --- | --- |
| `output/output.json` | Assignment-shaped JSON only. No audit fields. |
| `output/audit.json` | Page, span, method, identity link, warnings, quarantine, money checks. Sensitive. |
| `output/run.json` | Model name, digest, cache mode (`fresh` or `cached`), canonical hash. |
| `output/*-raw.txt` | Raw model text per section. Sensitive. |
| `output/repeatability.json` | After `--repeatability`. |
| `output/cache/`, `ocr/`, `pages/`, `private/` | Local caches, page images, review copies. Sensitive. |

Every file above is gitignored and must stay on this machine. `scripts/package_zip.py` builds a source zip from an allowlist and cannot include any of them. Clarion asked for the JSON plus feedback, not source code.

`success` is true only when every section returned schema-valid model JSON and every page that needed OCR produced text. It does not mean a person has verified the facts.

## Extraction rules

- Empty strings, empty lists, `{}` and `0` (doses) are the schema's missing values.
- A kept value must be a span on its cited page. Whitespace may be ignored when matching (for OCR of text printed without word gaps); every other character must match. Comma-joined lists from the model are split and each item grounded on its own. A value with no span is dropped and listed in the audit.
- Dates are kept only on the same line as an admission, discharge, or room-date label. A visit date, a length of stay, and a signature date are audit observations and never fill `doa`, `dod`, or a room end date.
- A prescription is merged only when the patient name is on that page and no other person is labeled there. Otherwise it is quarantined in the audit as an unverified link.
- Bank details are copied into `Patient_bankinfo` only when the account holder matches the patient (or `--associate-bank`). A different holder stays in the audit as an unverified relationship. The code does not call it fraud.
- Bill line items need a grounded line total plus a grounded bill group or bill category. When the page has a reconstructed table, labels are moved to the printed column they sit under; no text is added. The printed subtotal is kept; a different line-item sum is a `printed_subtotal_mismatch` warning. The claimed total is printed medical subtotal plus printed room total. Nothing is rewritten to reconcile.
- Template sample amounts are excluded when they appear only on a template page.
- An OCR engine score is copied onto evidence only when Paddle returned one. It is a recognition score, not factual or medical correctness.

## Offline check

Cursor's agent needs the internet, so run this yourself in a normal PowerShell window after turning Wi-Fi off:

```powershell
cd d:\clarion-local-pipeline
.\.venv\Scripts\python.exe scripts\offline_check.py
```

It refuses to start while any external host resolves or accepts a connection, runs fresh OCR (new work directory under `private\`) and fresh inference, probes the network every 20 seconds, records which processes hold public sockets, and writes `private\offline-test\result-*.json`. It passes only if the network stayed unreachable, no Python or Ollama process held a public socket, and the run succeeded. This passed on this laptop (see `TEST_REPORT.md`).

## Troubleshooting

**`ZoneInfoNotFoundError: Asia/Kolkata`.** Install `requirements.txt` again; it adds `tzdata` on Windows.

**`Ollama did not return a digest`.** Fixed in this revision. Ollama 0.40 omits `digest` from `/api/show`; the digest is now read from `/api/tags`.

**Model not found.** `ollama list` does not show `mistral:7b`. Pull it once on a network that allows it. No other model is called and `success` stays false.

**Connection refused.** Start the Ollama app. The client does not read `OLLAMA_HOST` and refuses redirects and non-loopback hosts.

**Low RAM.** Close WSL (`wsl --shutdown`) or browser tabs, or use the `--ocr-only` then `--fresh` sequence above. `keep_alive` is 0, so the model unloads after each section call.

**Paddle warnings.** "No ccache found" and "Could not find files for the given pattern(s)" are printed by paddle/paddlex on Windows and did not affect results.

**Long-running commands from an agent terminal.** In this session, commands launched from the agent's terminal were sometimes terminated when the agent's wait limit expired. Run long jobs (pull, full runs) in your own terminal.

## Limits

- One document was run. No accuracy figure is claimed; the output was reviewed by hand against the rendered source pages, not scored.
- OCR keeps recognition errors. On the scanned history page, at least one dose and one allergy word were misread by OCR and are copied as recognized; the audit carries the engine score.
- Text printed without word gaps stays without gaps in the JSON.
- The model swapped the two bill label columns on several fresh runs; the table check moved them back. If a page has no reconstructable table, that check cannot run.
- Partial copies are possible: a kept value is a true span but can be shorter than the full printed cell or sentence.
- Bill rows are ordered by where their amount first appears on the page, not strictly by table row.
- Repeatability was measured on this laptop only (6 fresh runs and 5 cached runs matched). Other hardware or Ollama versions may differ.
- IFSC is pattern-checked only. ICD codes are kept only when printed.
- PyMuPDF 1.26 is AGPL or Artifex commercial. Confirm the license before redistributing.
