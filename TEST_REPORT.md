# Test report

Target laptop, 7 Nov 2026: Windows 11 10.0.26200, Python 3.13.9, pytest 9.2.0, Ollama 0.40.15, `mistral:7b` digest `6577803aa9a0…bfbc1cf` (Q4_K_M), PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 on CPU.

There are three kinds of evidence below. Keep them apart:

1. **Unit tests**: synthetic text, synthetic PDFs, a fake chat function. No Ollama, no Paddle weights, no real document.
2. **Live integration checks**: the real Ollama model and real PaddleOCR on synthetic inputs.
3. **Real-document runs**: the assignment PDF with real OCR and real Mistral. Results were reviewed by hand against the rendered pages. No accuracy score is claimed.

No document values appear in this report.

## 1. Unit tests (mocked or synthetic)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

**23 passed in 1.46 s.** First run on this laptop failed at collection (`ZoneInfoNotFoundError`, missing `tzdata` on Windows); after the fix the original 18 passed. Five tests were added for the fixes in this revision.

| Test | Kind | Result |
| --- | --- | --- |
| `test_schema_defaults_and_extra_keys` | synthetic | Pass |
| `test_span_must_be_on_the_cited_page_whitespace_only` | synthetic | Pass |
| `test_dates_are_not_derived_and_must_be_labeled` | synthetic | Pass |
| `test_explicit_admission_and_discharge_are_kept` | synthetic | Pass |
| `test_template_amounts_are_contamination_not_a_bill_conflict` | synthetic | Pass |
| `test_sentinel_amount_can_remain_on_a_non_template_page` | synthetic | Pass |
| `test_printed_30900_is_kept_when_lines_sum_to_30400` | synthetic | Pass |
| `test_bill_line_needs_amount_and_one_grounded_label` (new) | synthetic | Pass |
| `test_swapped_bill_labels_follow_the_printed_table_header` (new) | synthetic | Pass |
| `test_unlinked_prescription_and_bank_are_not_merged` | synthetic | Pass |
| `test_ocr_engine_score_is_not_invented` (restructured; no longer skips its score checks when Paddle is installed) | synthetic | Pass |
| `test_rotated_table_rows_keep_wrapped_cells_together` (new) | synthetic OCR lines | Pass |
| `test_history_items_are_split_grounded_and_not_duplicated` (new) | synthetic | Pass |
| `test_native_pdf_text_and_template_classification` | synthetic PDF | Pass |
| `test_mocked_pipeline_excludes_template_and_keeps_printed_total` | mocked model | Pass |
| `test_cache_versus_fresh_and_digest` | mocked model | Pass |
| `test_fresh_five_calls_model_cached_five_does_not` (now also checks per-run claims and `differing_fields`) | mocked model | Pass |
| `test_invalid_json_retries_then_model_errors_are_visible` | mocked model | Pass |
| `test_partial_ocr_does_not_report_success` | mocked model | Pass |
| `test_assignment_profile_does_not_silently_use_short_pdfs` | mocked model | Pass |
| `test_non_loopback_host_is_refused` | no network | Pass |
| `test_digest_falls_back_to_tags_when_show_has_none` (new) | mocked HTTP | Pass |
| `test_sample_structure_is_not_an_extraction` | synthetic | Pass |

`success: true` inside a mocked test only means the fake model's JSON survived the checks.

## 2. Live integration checks (real model / real OCR, synthetic input)

| Check | Command | Result |
| --- | --- | --- |
| Short model request | `POST /api/generate`, 8 tokens | Pass. Load 7.3 s, total 8.6 s. |
| Model digest | `scripts/live_model_check.py` | Pass after fix. Before the fix, `/api/show` on Ollama 0.40.15 returned no digest and every live run would fail. |
| Schema-constrained call through `extract_section` | same | Pass. Valid `BillsSection` JSON with exactly the two synthetic lines, 29.3 s. |
| Missing model | same | Pass. `OllamaError` with HTTP 404. |
| Closed loopback port | same | Pass. `OllamaError` "not reachable". |
| Non-loopback host | same | Pass. Refused before any request. |
| PaddleOCR on a synthetic image, first use | `recognize(..., "paddle")` | Pass. Downloaded two PP-OCRv4 mobile models (about 12 MB), 3/3 lines, engine scores 0.976–0.992, 28 s. One artifact: a trailing period was added to a number. |
| PaddleOCR from disk | same | Pass. 5.6 s, no download. |
| Installed Paddle API vs adapter | `inspect.signature(PaddleOCR)` | `predict` present, returns `rec_texts`, `rec_scores`, `rec_boxes`, `rec_polys`. Adapter arguments accepted. |

## 3. Real document: `input/record.pdf`, profile `clarion-assignment`

Pages 1–5 were never read. Pages 6, 10, 12 used the text layer; pages 7, 8, 9, 11 were OCR'd (two dark panels were inverted first). Seven sections: identity, history, opd, bank, visit, bills, room.

### Final fresh run (run 4, also identical in 5 repeatability runs and the offline run)

| Check | Result |
| --- | --- |
| All sections returned schema-valid model JSON | Yes (7/7). `success: true`. |
| `validate.py` schema | Valid, no errors. |
| Evidence spans | 51 evidence items, all verified as slices of the cited page. |
| Dropped values | None in the final run. |
| Bill lines | 8 lines, which matches the 8 printed rows. Totals, discounts, quantities, and remarks match the printed table on review. |
| Bill labels | The model swapped Bill Category and Bill Group on all 8 lines; the table-header check moved them back and recorded `bill_labels_moved_to_printed_columns`. |
| Money | Line sum 30400, printed medical subtotal 30900, `printed_subtotal_mismatch` with difference 500. Printed subtotal kept, lines not edited. Printed room total kept. `total_claimed_amount` = printed subtotal + printed room total = 69900. `validate.py` also reports line items plus room (69400) vs claimed (69900). |
| Room arithmetic | rate × days − discount equals the printed room total. No warning. |
| Template amounts | Not present (pages 1–5 excluded by the profile). |
| Dates | `doa` and `dod` empty: the record prints a visit date, a length of stay, and a signature date, none labeled as admission or discharge. Those are audit observations only. |
| Identity | Prescription page names a different person: medicines quarantined in the audit (`prescription_quarantined`), `opd_details` empty. Cheque holder differs from the patient: bank fields kept in the audit only (`bank_not_linked`), `Patient_bankinfo` empty. Neither is described as fraud. |
| TPA | Empty; the record prints an insurance id, not a TPA id. |
| ICD | Empty; no codes are printed. |
| Missing values | Fields not printed (for example registration number of the hospital, doctor qualification, patient phone and email) are `""`. |
| Known residual issues | OCR misread at least one dose and one allergy word on the scanned history page, and those are copied as recognized. History text printed without word gaps stays without gaps. Some kept values are shorter than the full printed cell. Bill rows are ordered by first amount occurrence, not strictly by table row. |

### Earlier fresh runs on this document (before the fixes)

| Run | `success` | Honest status |
| --- | --- | --- |
| 1 | true | Incorrect bills (14 rows from split table rows) and empty history, diagnosis, treatment. Defects in OCR table grouping and grounding. |
| 2 | true | Bills empty: correctly read lines were dropped by an over-strict mapper rule. |
| 3 | true | Bill labels swapped by the model. |

These show that `success: true` does not mean the content is right. They were kept locally for comparison.

## Repeatability (this laptop only)

| Test | Runs | Result |
| --- | --- | --- |
| Fresh, response cache not read or written (`--repeatability --repeatability-runs 5`) | 5 | All canonical hashes equal (`ad77aca9…d9a2`), `differing_fields: []`. 2514 s total. Also equal to fresh run 4 and to the offline fresh run, so 7 fresh runs matched. Raw model text for all seven sections was byte-identical between run 4 and the fifth repeatability run. |
| Cached (no `--fresh`) | 5 | All `inference=cached`, 0 model calls, 1.2–1.7 s each, same hash. These say nothing about model variance. |

Temperature 0, `top_k` 1, and seed 42 produced identical output here. That is an observation on one machine, one Ollama version, and one model build, not a guarantee.

## Disconnected network

| Attempt | Result |
| --- | --- |
| Pre-check while the user believed Wi-Fi was off | DNS and HTTPS reachable. Nothing run. |
| `scripts/offline_check.py` while online | Refused, exit 3. |
| Attempt 1, 07:16–07:27 | Run completed (exit 0, success, fresh OCR on 4 pages, fresh inference, same hash), external hosts unreachable at every probe. **Script verdict: did not pass**, because three stale public sockets owned by Cursor.exe and OneDrive.exe were counted. Script changed to attribute sockets to processes. |
| Attempt 2, 07:32:59–07:41:40 | **Passed.** `offline_verified: true`. 25 probes, DNS and HTTPS to external hosts failed at every probe, no Python or Ollama process held a public socket, fresh OCR on pages 7, 8, 9, 11 (4 new OCR cache files, no errors), 7/7 sections extracted, success true, hash equal to the online fresh runs, 520 s. |

## Not run

| Item | Why |
| --- | --- |
| Poppler fallback | PyMuPDF imported. |
| RapidOCR | Not installed. |
| Linux or macOS for this revision | Not available. |
| Any other document | Only one PDF was provided. No accuracy figure is claimed. |
