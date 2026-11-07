# Approach

## What this is

A single local process reads a PDF, keeps a text layer when it exists, OCRs pages that look like scans, asks a loopback Ollama model for JSON, and then drops anything it cannot point at on the page. The assignment JSON and the audit file are separate. The JSON has only the assignment keys. `success: true` means every requested section returned schema-valid model JSON and every page that needed OCR produced text. It does not mean a human has checked the facts.

The folder layout follows the work: PDF and OCR processors, one model caller, identity linking, field mapping, a schema, and two validators. Some names from a longer checklist were not added. Poppler is a fallback inside `processors/pdf_processor.py`, not a second product. There is no web UI, database, or retrieval index.

## Why this shape

The assignment's sample object is not valid JSON. Curly quotes, missing commas, and an invoice array with no name are repaired in `schemas/medical_schema.py`. The odd keys are kept: `ifsi_code`, `dosagefrequencyTotalDays`, `Patient_bankinfo`, `medicalhistory`. The invoice array is named `bills` because the sample never named it and the objects are bill lines. Placeholder values in the brief are not defaults. `success` defaults to false so an empty object cannot look finished.

PyMuPDF is the reader (1.26.7). It returns the text layer and a page image. pdfplumber 0.11.10 is used for native words and for a table if the PDF actually has one. Poppler remains the fallback if `fitz` does not import; that path was not exercised. PaddleOCR 3.7.0 (PP-OCRv4 mobile detection and English mobile recognition) is called only for pages the classifier marks as scans, on CPU. It was run on the target Windows laptop for this revision, online and offline.

Ollama is called with `format` set to the section's JSON schema, temperature 0, seed 42, `top_k` 1, and `keep_alive` 0. Those options are part of the request. They are not a promise that two machines emit the same tokens. The canonical hash sorts keys and strips insignificant whitespace. It does not repair a different answer. The model digest comes from Ollama's own model list: Ollama 0.40 confirms the model through `/api/show` but returns the digest only in `/api/tags`.

## Scanned tables

The assignment's bill table is a screenshot rotated about two degrees, with cells that wrap onto two or three lines. Grouping OCR lines by a fixed vertical tolerance split each printed row into three or four text rows, and the model then produced duplicate bill lines (one from the unit price, one from the total). The OCR adapter now keeps each line's polygon. `table_rows_to_text` estimates the local rotation as the most common slope between horizontally separated lines in the same neighborhood (single-word angles were too noisy), aligns rows on that rotation, stacks wrapped lines into the cell above them, and merges adjacent digit-free header rows. It only regroups recognized text; it never edits it. The result is a `[TABLE]` block that is added to the page text only when at least two rows have three or more cells. The bill and room prompts tell the model to read rows from that block and to match columns by header name.

Models still mislabel columns. When a bill line's total and labels match exactly one row of a reconstructed table whose header names "Bill Category" and "Bill Group", each label the model returned is moved to the column it is printed under, and the audit records `bill_labels_moved_to_printed_columns`. A label the model did not return is not filled in.

## Page range and template pages

Generic mode reads every page. The assignment profile selects pages 6–12, which is the record portion of that one 12-page file. A shorter PDF does not silently lose its first pages: if pages 6–12 do not exist, the run stops with `success: false` and does not call the model.

Instruction pages are also classified by phrases such as "example schema only" and "medical document processing". Auto mode removes them from the prompt. The amounts 62042 and 66666 are the brief's sample figures. If they show up only on a template page they are dropped and the audit code is `template_contamination_excluded`. That is not a bill-math warning. If a non-template page really prints 62042, the amount can stay.

The bill-math warning is different. Grounded line amounts are summed. A printed subtotal is read from the page. If those differ, the printed subtotal is kept and `printed_subtotal_mismatch` is recorded. Lines are not changed. The assignment case is lines summing to 30400, a printed medical subtotal of 30900, and a printed room total of 39000. The claimed amount becomes 69900, which is the sum of the two printed components, and the 500 gap is the warning. A model total of 69400 is not substituted in.

## Evidence

The model returns field values. It does not get to attach a citation that we trust. For each kept string, the mapper finds that text on an allowed page. The search collapses whitespace. The stored span is the page's own slice, with start and end offsets, and a second check confirms the slice is on that page. If the value is not on the page, the field becomes an empty string or the row is dropped.

Two fallbacks were added after the first real run, and both still require a page span. First, a whitespace-insensitive match: the scanned history panel prints words without gaps, and some values wrap across lines. Every non-whitespace character must match in order; the kept text uses the model's spacing only when its characters equal the span exactly. Second, a list split: the model often returns several printed items as one comma-joined string. The string is split on commas and semicolons, adjacent pieces are re-joined when the page prints them as one item, and each resulting item is grounded on its own. For the history dictionaries, the model sometimes put the item in the key; the mapper tries "key value", then the value, then the key when the value is empty. A medical-history item whose span is already kept as an allergy or current medication is dropped from `medicalhistory`, so one printed line does not land in two places.

A bill line needs a grounded line total plus a grounded bill group or bill category (previously the group was mandatory, which discarded correctly printed lines whenever the model left the group empty).

Dates are narrower. A date is kept only when the same line has an admission label, a discharge label, or a room start/end label. A visit line, a "length of stay" line, and a bare "Date:" line are observations in the audit. They do not fill `doa`, `dod`, or a room end date. Month names are normalized to `DD/MM/YYYY` only after that line match. Missing strings stay `""`. Missing lists stay `[]`. A missing dose stays `0`.

OCR boxes and the engine's own score are copied onto an evidence item only when the span sits inside an OCR line that carried that score. The field is `ocr_engine_score`. It is a recognition statistic. It is omitted when the engine did not provide one. It is not a correctness score.

## Identity

Prescription items stay on the patient record only when the patient name is on that same page and the page does not label a different person. Otherwise they are quarantined with the page span and the reason. Bank fields are extracted as printed, including the account holder, and copied into `Patient_bankinfo` only when the holder name matches the patient. `--associate-bank` copies them anyway and the audit says the association was forced and is unverified. A different holder is not described as fraud. An insurance id is not copied into `tpa_id` unless the page says TPA. ICD codes are kept only when the code itself is printed.

No IFSC directory is queried. A pattern check in the factual validator is the only IFSC test. A directory lookup done by a person earlier is not applied and is not treated as the page text.

## Cache

The response cache key is a hash of the pipeline version, model name, model digest, generation options, policy version, processing settings (pages, template mode, DPI, OCR revision, PDF backend), prompt hash, schema hash, and source hash. The digest comes from Ollama's local `/api/show`. If that call fails, extraction stops and `success` is false. Prototype cache files without `cache_version` 2 are counted and ignored.

`--fresh` does not read the cache. `--repeatability` does not read or write it, so a five-run comparison cannot be a cache echo. Each repeatability run's claim is saved locally and the report lists the field paths that differ between runs, without values. A later run without `--fresh` is labeled `cached` and is not evidence about model variance.

The OCR cache (`ocr/page-NN.json`) is keyed by PDF hash, page, backend, DPI, and preprocessing revision. Its version was raised to 3 when polygons were added, so older OCR files are re-run instead of reused.

## Failure

Model HTTP errors are not retried. Invalid JSON is retried once. If OCR is required and produces no text, that page is withheld from the model instead of sending a heading and hoping. The final `success` flag is false if any section failed or any required OCR page is empty, even when other fields were filled. The claim message includes the section error text. Schema validation and the factual checker are separate commands in `validate.py`. A schema-valid file can still carry a 500 money warning.

## What was not chosen

A second OCR stack was not required. RapidOCR remains a flag that errors clearly if the package is absent. It was not tested. PP-Structure table recognition, document unwarping, and a handwriting model were not added; each is an extra weight download, and the geometric row rebuild was enough for the one rotated table in this file. No GUI was added. A larger or different LLM was not tried; `mistral:7b` fit in memory on this laptop.
