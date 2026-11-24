# Document upload, reading, reload and Q&A fixes (24 Nov 2026)

## Why DOC/DOCX uploads failed (root causes)

1. **The zip-bomb guard rejected ordinary DOCX files.** `validation._check_zip` rejected any part
   whose compression ratio exceeded 100:1. Real Word files routinely contain such parts: tables with
   many empty cells (`word/document.xml` ≈ 170:1), blank or simple BMP/EMF/TIFF images (≈ 1000:1). Those
   uploads failed with `400 CORRUPT_FILE · possible zip bomb`. A ratio alone cannot tell a blank image
   from a bomb; what matters is how much the file expands to. **Fix:** limit the total expanded size
   (400 MB) and member count (20 000); a ratio check now applies only to members over 50 MB.
2. **`.doc` was not supported at all** (415 in the API, hidden by the file picker), and renamed files
   (`.doc` saved as `.docx` and vice versa) failed the magic-byte check. **Fix:** Word files are
   identified by content (ZIP → DOCX, OLE → DOC, `{\rtf` → RTF). New `.doc` reader: built-in pure
   Python Word 97 reader (piece table, fields, tables, footnotes, text boxes), plus LibreOffice when
   installed. Password-protected Word files get a clear `400 ENCRYPTED_FILE`.
3. **DOCX text was silently lost.** python-docx's simple body walk skipped content controls
   (`w:sdt`: forms, templates, cover pages), tracked insertions, text boxes/shapes, field results,
   footnotes/endnotes, headers/footers and list numbering; merged-cell de-duplication by text dropped
   legitimate equal values (`5 | 5`) and shifted columns. Documents made mostly of content controls
   were rejected as "no extractable text". **Fix:** new namespace-agnostic XML reader
   (`app/ingestion/extractors/word.py`), also handling Strict OOXML and non-standard main-part names.

Also fixed: PDF table detection ran an ML layout model and one text extraction per table cell on every
page (267-page PDF: 51 s). Long PDFs now use the vector-line detector and skip pages without drawings
(same tables found; 51 s → 4 s), so large documents finish inside the timeout.

## New features

| Area | What changed |
| --- | --- |
| Upload | Client-side validation (type, size, empty), byte-level progress bar, "processing" stage, cancel, file type/size/status per file, clear server error messages; `GET /documents/limits`. Limits 50 MB / 1000 pages. |
| Reading | `GET /documents/{id}/content` and a **View text** viewer: headings (as section path), paragraphs, numbered/bulleted lists, tables, footnotes, in reading order. |
| Reload | Originals stored in a new `document_files` table (created automatically, no migration). `POST /documents/{id}/reload` re-extracts and re-indexes in place; a failed reload keeps the working index and shows the error. Older documents are re-indexed from stored text. |
| Q&A | When the selected document fits (`FULL_CONTEXT_MAX_TOKENS`, 12 000) the whole document is sent to the model; larger ones use hybrid retrieval. Missing answers reply exactly "This information is not available in the uploaded document." (Urdu/Arabic equivalents). Prompt asks for complete, detailed answers; `LLM_MAX_TOKENS` 600 → 1000. Citations are still verified in code. |
| UI | Documents page: Status column, Ask / View text / Reload / Delete. Chat: selected-document header (name, type, status, View text, Reload), "read whole document" / "top passages" label, chat and selection restored after navigation or browser refresh. |
| Logging | Upload rejections, reloads and failures logged with error code, file type and chunk count. |

## Tests

161 tests pass (was 133): `tests/unit/test_word_formats.py` (20 regression tests, including the
compressible-image DOCX that used to fail, empty table grids, renamed and password-protected files,
content controls/tracked changes/fields/text boxes/lists, merged cells, footnotes/headers, Strict
OOXML, a real Word 97 `.doc`, RTF, and a 6 000-paragraph + 2 000-row DOCX) and
`tests/api/test_documents_workflow.py` (upload of every format, content, reload, full-document Q&A,
exact refusal, multi-question sessions, delete).
Also checked by hand over HTTP: 374-chunk DOCX (1 s), same as `.doc` (3.6 s), 267-page PDF (2.6 s),
restart persistence, and a headless-browser walkthrough of upload → view → reload → ask → refresh.
Answer quality was not checked with a live model (no API key in the build environment).
