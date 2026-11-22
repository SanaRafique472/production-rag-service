"""Create the pipeline notebooks (run once; then execute with `make notebooks`)."""

from pathlib import Path

import nbformat as nbf

SETUP = """import sys, uuid, json
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT / "backend"))
from tests.fixtures.samples import build_samples
SAMPLES = build_samples(ROOT / "data" / "samples")
for name, path in SAMPLES.items():
    print(f"{name:14s} {path.name:22s} {path.stat().st_size:>7} bytes")"""


def nb(cells: list[tuple[str, str]]) -> nbf.NotebookNode:
    book = nbf.v4.new_notebook()
    book.cells = [nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src) for kind, src in cells]
    book.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return book


NB01 = nb([
    ("md", "# 01 · Extraction and cleaning\n\nGoal: show that every supported file type is read **with its location** "
           "(page/slide or heading section), that tables stay as aligned rows, and that cleaning removes noise "
           "(running headers, page numbers, broken hyphenation) without touching real content or Urdu text."),
    ("code", SETUP),
    ("md", "## 1. PDF: raw extraction\nEach unit is a paragraph or table with its page and heading path. "
           "Note the running header and the page-number footer on every page."),
    ("code", """from app.ingestion.extractors.pdf import extract_pdf
raw = extract_pdf(SAMPLES["policy_pdf"].read_bytes(), max_pages=300)
print("pages:", raw.page_count)
for u in raw.units:
    print(f"p{u.page} [{u.kind:5s}] section={u.section!r:22s} | {u.text[:70]!r}")"""),
    ("md", "## 2. Cleaning\nHeaders repeated on ≥60% of pages and page-number lines are dropped; "
           "`por-\\ntal` becomes `portal`; single line breaks inside paragraphs become spaces."),
    ("code", """from app.ingestion.cleaning import clean_units, text_stats
clean = clean_units(raw.units, raw.page_count)
print("before:", dict(text_stats(raw.units)))
print("after: ", dict(text_stats(clean)))
removed = {u.text for u in raw.units} - {u.text for u in raw.units if any(u.text.split()[0] in c.text for c in clean)}
print("\\nremoved lines:", sorted(removed))
print("\\nhyphen repaired:", "HR portal" in " ".join(u.text for u in clean))"""),
    ("md", "## 3. Tables become Markdown rows\nCells are read from their own rectangle on the page, so words keep their spaces."),
    ("code", """print(next(u for u in clean if u.kind == "table").text)"""),
    ("md", "## 4. DOCX: headings give the section path (DOCX has no fixed pages)"),
    ("code", """from app.ingestion.extractors.office import extract_docx, extract_pptx
for u in extract_docx(SAMPLES["handbook_docx"].read_bytes()).units:
    print(f"[{u.kind:5s}] {u.section!r}\\n        {u.text[:80]!r}")"""),
    ("md", "## 5. PPTX, Markdown and Urdu text (bonus file types)"),
    ("code", """from app.ingestion.extractors.text import extract_text
for u in extract_pptx(SAMPLES["slides_pptx"].read_bytes(), max_pages=300).units:
    print(f"slide {u.page} section={u.section!r}: {u.text!r}")
for u in extract_text(SAMPLES["notes_md"].read_bytes(), markdown=True).units:
    print(f"md section={u.section!r}: {u.text!r}")
for u in clean_units(extract_text(SAMPLES["urdu_txt"].read_bytes(), markdown=False).units, None):
    print("urdu:", u.text)"""),
    ("md", "## 6. Bad uploads are rejected with the right status code"),
    ("code", """from app.errors import AppError
from app.ingestion.pipeline import process_file
cases = {"empty.pdf": SAMPLES["empty_pdf"], "invoice.pdf (renamed .exe)": SAMPLES["fake_pdf"],
         "scanned.pdf": SAMPLES["scanned_pdf"], "setup.exe": SAMPLES["fake_pdf"]}
for label, path in cases.items():
    name = label.split(" ")[0]
    try:
        process_file(name, path.read_bytes(), document_id=str(uuid.uuid4()))
        print(f"{label:28s} accepted (unexpected)")
    except AppError as e:
        print(f"{label:28s} -> {e.status_code} {e.code}: {e.message}")"""),
])

NB02 = nb([
    ("md", "# 02 · Chunking\n\nCompare the **proposed** strategy (B: section-aware, ~400 tokens, 60 overlap, "
           "context header) with the **baseline** (A: recursive split over the whole text, 800/120). "
           "The evaluation in Phase 7 decides which wins; here we check that both behave as designed."),
    ("code", SETUP),
    ("md", "## 1. Real sample: the leave-policy PDF"),
    ("code", """from app.ingestion.pipeline import process_file
from app.ingestion.chunking import PROPOSED, BASELINE
data = SAMPLES["policy_pdf"].read_bytes()
for cfg in (PROPOSED, BASELINE):
    res = process_file("leave_policy.pdf", data, document_id=str(uuid.uuid4()), config=cfg)
    print(f"\\n=== config {cfg.name}: {cfg.strategy}, size {cfg.size}, overlap {cfg.overlap} -> {len(res.chunks)} chunks")
    for c in res.chunks:
        print(f"[{c.position}] pages {c.page_start}-{c.page_end} {c.kind:5s} {c.token_count:>3} tok | {c.section}")"""),
    ("md", "## 2. Metadata stored with every chunk (requirement: document id, file name, page/section, position)"),
    ("code", """from dataclasses import asdict
res = process_file("leave_policy.pdf", data, document_id=str(uuid.uuid4()), config=PROPOSED)
print(json.dumps(asdict(res.chunks[1]), indent=2, ensure_ascii=False))"""),
    ("md", "## 3. A longer document: sizes, overlap and section boundaries\nSynthetic text of ~1,500 tokens over two pages and two sections."),
    ("code", """from app.ingestion.extractors.base import TextUnit
from app.ingestion.chunking import chunk_document
sentences = [f"Rule {i} says employees must follow procedure number {i} carefully." for i in range(120)]
units = [TextUnit(" ".join(sentences[:60]), page=1, section="Rules > Part A"),
         TextUnit(" ".join(sentences[60:]), page=2, section="Rules > Part B")]
for cfg in (PROPOSED, BASELINE):
    chunks = chunk_document(units, document_id=str(uuid.uuid4()), filename="rules.pdf", config=cfg)
    print(f"config {cfg.name}: {len(chunks)} chunks, tokens = {[c.token_count for c in chunks]}, "
          f"pages = {[(c.page_start, c.page_end) for c in chunks]}")"""),
    ("code", """b = chunk_document(units, document_id=str(uuid.uuid4()), filename="rules.pdf", config=PROPOSED)
print("end of chunk 0  :", b[0].text[-120:])
print("start of chunk 1:", b[1].text[:120])
print("\\nB never mixes sections:", all(not ("Rule 59 " in c.text and "Rule 60 " in c.text) for c in b))
print("\\nembedded text of chunk 0 starts with the header:\\n", b[0].embed_text[:80])"""),
])

out = Path(__file__).resolve().parents[1] / "notebooks"
nbf.write(NB01, out / "01_extraction_and_cleaning.ipynb")
nbf.write(NB02, out / "02_chunking.ipynb")
print("written")
