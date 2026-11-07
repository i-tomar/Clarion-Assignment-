# Work log

This file lists commands that were actually run. Part 1 is the earlier Linux build machine (no live model). Part 2 is the target Windows laptop on 7 Nov 2026 (times IST), where live OCR and Mistral runs happened. No document values are recorded here.

# Part 1: earlier build machine (Linux)

Times are the shell clock on the build machine.

## Before the privacy stop

The uploaded assignment PDF, a prototype zip, a manual reference JSON, a feedback note, and a research note were already in this environment.

- Listed the prototype zip and unpacked it under `/tmp` to read its Python files. The prototype is one script plus a schema and a validator. It always treated pages 6–12 as the record, could merge an unrelated prescription, could fill a discharge date from a stay, and could set `success` in ways this revision does not.
- `pdfinfo` was not installed. `apt-get install poppler-utils` completed. `pdfinfo` reported version 24.02.0 and 12 pages.
- The assignment PDF and the prototype's `input/record.pdf` were the same file by checksum. `pdftotext` was run on that PDF, and the prototype's OCR JSON was read, so page roles and the broken sample schema could be seen.
- That text and those OCR files contain the record. They are not copied into this tree. The temporary unpack directory was later deleted.
- `python3 -m venv` failed until `python3.12-venv` was installed. `.venv` was created and `pydantic==2.14.3` was installed. `python --version` was 3.12.3.
- Ollama was not on `PATH`. `http://127.0.0.1:11434/api/tags` returned connection refused. PaddleOCR was not importable. No model weights were pulled. An earlier environment had already reported that `ollama pull mistral:7b` was blocked by a non-public redirect. That block was not bypassed.

## After the privacy stop

No further read of the assignment PDF, its OCR, or the reference JSON. Tests and examples use synthetic names and amounts only.

- Installed pytest, pdfplumber, and PyMuPDF. The first PyMuPDF wheel was 2.1 and pulled layout packages. It was replaced with `PyMuPDF==1.26.7`, which is the reader the tests import. pdfplumber is 0.11.10. pytest is 9.2.0.
- Moved the started modules into `processors/`, `extraction/`, `schemas/`, `validation/`, `prompts/`, and `config/`. `main.py` is the CLI. `extract.py` calls the same function.
- Tightened three rules after the last review: a kept quote must be a span on the cited page (whitespace collapsed only for the comparison); 62042 and 66666 are template contamination, not a bill conflict; any missing OCR page or failed model section forces `success: false` and puts the error in the message.
- Name matching on a prescription was matching across a newline (`Asha Rao` plus the next token). The person pattern now stops at the end of the line. A unit test covers a linked medicine and a quarantined one.

## Tests and environment check

Command:

```sh
.venv/bin/python -m pytest tests -v --tb=line
```

Result: 18 passed in 0.74s. Platform line from pytest: `linux -- Python 3.12.3, pytest-9.2.0`.

Command:

```sh
.venv/bin/python scripts/check_env.py
```

Result:

- python 3.12.3, Linux 6.12.113+, 4 CPUs
- `pdfinfo`, `pdftotext`, and `pdftoppm` on PATH
- `ollama` MISSING
- imports: pydantic yes, fitz yes, pdfplumber yes, paddleocr no
- loopback Ollama: connection refused

`check_env.py` did not open a PDF and did not pull weights.

## Not done

- No `ollama pull`.
- No PaddleOCR install and no OCR `predict`.
- No run of the assignment PDF after the privacy stop.
- No disconnected-network proof. Weights are not on this machine.
- Windows and macOS were not used.
- No email, no upload, no pull request.

## Package

```sh
.venv/bin/python scripts/package_zip.py /opt/cursor/artifacts/clarion-local-pipeline.zip
unzip -t /opt/cursor/artifacts/clarion-local-pipeline.zip
```

`unzip -t` reported no errors. The archive has 41 files. It has no PDF, no `.venv`, no OCR cache, and no weight file. `pdf` appears only in source names such as `pdf_processor.py`.

## Status at the end of part 1

Code, synthetic tests, and docs were in the tree. A real extraction JSON did not exist. `examples/sample-structure.json` validates against the schema and is labeled as a sample, not a generated extraction.

# Part 2: target laptop (Windows 11, 7 Nov 2026, IST)

## Inspection (04:28)

- `Get-CimInstance Win32_OperatingSystem / Win32_Processor / Win32_VideoController`, `Get-PSDrive`: Windows 11 Home 10.0.26200, i5-12450H 8C/12T, 15.7 GB RAM (3.7 GB free), Intel UHD only, C: 88.8 GB free.
- `python --version`, `py -0p`: CPython 3.13.9 only.
- `ollama --version`: 0.40.15. `ollama list`: empty. `GET http://127.0.0.1:11434/api/version`: reachable. `netstat -ano | findstr :11434`: one listener on 127.0.0.1, child of the tray app. No `OLLAMA_*` variables set.
- No PDF in the workspace; `input/` held only `.gitkeep`. Asked the user; the user placed `input/record.pdf` (12 pages). No other folders were searched.

## Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip          # pip 26.3
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -v --tb=short
```

Collection failed: `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'` (Windows has no IANA database). Fix: `tzdata` added to `requirements.txt` for Windows. Rerun: 18 passed.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

The pinned cp313 Windows wheels existed (`pip index versions`). The first foreground attempt hit the agent's 15-minute wait while the model download shared bandwidth; a rerun showed everything already installed. Installed: paddleocr 3.7.0, paddlepaddle 3.3.1, paddlex 3.7.2, numpy 2.3.5, opencv-contrib-python 4.10.0.84. Full list saved locally with `pip freeze`.

## Ollama

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD','1','User')
# stopped the tray app and its server, relaunched the tray app once
ollama pull mistral:7b
ollama show mistral:7b
Invoke-RestMethod -Method Post http://127.0.0.1:11434/api/show -Body '{"name":"mistral:7b"}'
Invoke-RestMethod http://127.0.0.1:11434/api/tags
Get-FileHash <blob> -Algorithm SHA256
```

- Server log after restart: `OLLAMA_NO_CLOUD:true`, "Ollama cloud disabled: true", "Listening on 127.0.0.1:11434". One listener only.
- The pull went to the normal registry (Cloudflare, public addresses), 16 parallel parts, finished about 04:58. No proxy or bypass.
- Model: digest `6577803aa9a0…bfbc1cf`, 7.2B, Q4_K_M, GGUF. The weight blob's computed SHA-256 equals its content address.
- `/api/show` returned no `digest` field. `fetch_model_digest` raised on every live run. Fix: confirm via `/api/show`, read the digest from `/api/tags`. Unit test added.
- Short request (`/api/generate`, 8 tokens): load 7.3 s, total 8.6 s.
- `scripts/live_model_check.py` (new): all six checks passed (digest, schema-constrained `extract_section`, 404 for a missing model, refused closed port, refused non-loopback host). The schema call took 29.3 s.

## OCR

- Synthetic image through `processors.ocr_processor.recognize(..., "paddle")`: first call downloaded the two PP-OCRv4 models (about 12 MB) and returned 3 of 3 lines in 28 s; second call loaded from disk in 5.6 s.
- `test_ocr_engine_score_is_not_invented` used `pytest.skip` when Paddle was installed, which also skipped its score assertions. Restructured so only the "Paddle absent" branch is conditional.

## Real PDF runs

```powershell
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --ocr-only
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --fresh
```

| Run | Time | Result | What was wrong, what changed |
| --- | --- | --- | --- |
| OCR only | 05:22, 77 s | 4 scanned pages, 0 failures | Two dark panels inverted by preprocessing. |
| Fresh 1 | 05:24–05:34, 614 s | success=true, but wrong | 14 bill rows (duplicated per printed row), line sum far from the printed subtotal; history, diagnosis, treatment empty; room discount missing. Cause: OCR row grouping split wrapped cells of a rotated table; model packed list items into one string or into keys; model returned treatment as a JSON string. |
| Fixes | | | Rotated-table row rebuild with polygons (OCR cache v3); whitespace-insensitive and list-split grounding; key/value fallback; history overlap removal; prompt rules for history, diagnosis, treatment, tables. |
| Fresh 2 | 05:52–06:00, 483 s | success=true, bills empty | Model filled bill_category but left bill_group empty; the mapper required bill_group and dropped all 8 lines. Fix: amount plus group or category. Room prompt: copy printed days into total_days. |
| Fresh 3 | 06:03–06:11, 534 s | success=true | 8 lines, line sum 30400 vs printed 30900, warning kept. Model swapped bill_group and bill_category on every line. Fix: move labels to their printed column using the reconstructed table header. |
| Fresh 4 | 06:15–06:23, 507 s | success=true | Labels moved on 8 lines (warning recorded). Schema valid. Hash `ad77aca9…d9a2`. |

After each run the outputs were copied to `private/runs/<run>/` before the next run overwrote `output/`.

```powershell
.\.venv\Scripts\python.exe validate.py output/output.json --audit output/audit.json
```

Schema valid, no errors. Factual warnings: line items plus room 69400 vs claimed 69900, printed subtotal mismatch 500, prescription quarantined, bank not linked, bill labels moved.

## Repeatability

```powershell
.\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment --repeatability --repeatability-runs 5
1..5 | % { .\.venv\Scripts\python.exe main.py input/record.pdf --profile clarion-assignment }
```

- Fresh: 06:26–07:07, 2514 s. Five hashes equal (`ad77aca9…d9a2`), `differing_fields` empty, same as fresh run 4. Raw model text for all seven sections was byte-identical between run 4 and the fifth repeatability run.
- Cached: five runs, 1.2–1.7 s each, `inference=cached`, zero model calls, same hash.
- `--repeatability` now saves each run's claim and lists differing field paths.

## Disconnected network

- The first request to disconnect was answered while Wi-Fi was still up; the check showed DNS and HTTPS reachable, so nothing ran.
- Turning Wi-Fi off also cuts the Cursor agent, so `scripts/offline_check.py` (new) was written for the user to run in their own terminal. While online it refused to run (exit 3).
- Attempt 1 (07:16–07:27, 657 s): DNS and HTTPS unreachable at every probe; run succeeded with fresh OCR and fresh inference, same hash. The script's verdict was "did not pass" because it counted three stale ESTABLISHED sockets that existed before the run. `Get-CimInstance Win32_Process` showed their owners were Cursor.exe (two) and OneDrive.exe. The script was changed to attribute sockets to processes and to fail only on Python or Ollama sockets.
- Attempt 2 (07:32:59–07:41:40, 520 s): `offline_verified: true`. 25 probes, external hosts unreachable at all of them, no Python or Ollama public socket, one stale Cursor.exe socket listed. Fresh OCR on 4 pages, 7 sections extracted, success=true, hash equal to the online fresh runs.

## Tests at the end

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

23 passed (Python 3.13.9, pytest 9.2.0, win32).

## Not done

- No email, upload, repository publish, deployment, or zip was produced.
- Poppler fallback, RapidOCR, macOS, Linux on this revision: not run.

# Part 3: targeted accuracy fixes (7 Nov 2026, 08:10–08:38 IST)

- 08:10 checkpoint of code, docs, JSON, audit to `private/checkpoint-20261107-0810/`.
- Added visit-page OPD section (page 10), profile primary-record pages {6, 7, 10, 11, 12}, drug numbers from the medicine's own line, procedure-heading split, room cell completion, `--ocr-overrides` with two human-reviewed page-7 lines (`private/ocr_overrides.json`), and removal of prescribed-medicine lines from `treatment_taken`.
- `pytest tests -q`: 26 passed.
- `main.py input/record.pdf --profile clarion-assignment --fresh --ocr-overrides private/ocr_overrides.json`: 08:14–08:23 (564 s), then after the treatment fix 08:25–08:34 (559 s), hash `e5cd3283…`. Both success, 8/8 sections.
- `validate.py`: schema valid. Field change record: `private/submission/field-change-record.json` (35 changed values, all generated).
- Repeatability and offline runs were not repeated for this version.
