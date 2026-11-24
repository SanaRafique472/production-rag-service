"""PDF extraction with PyMuPDF.

For each page we:
1. find tables and export them as Markdown (so rows and columns stay aligned),
2. read the remaining text blocks in reading order, skipping text inside tables,
3. treat short lines in a larger or bold font as headings, which gives each
   paragraph a section path for chunking and citations.
"""

import statistics

import pymupdf

from app.errors import CorruptFileError, FileTooLargeError
from app.ingestion.extractors.base import (
    ExtractedDocument,
    SectionTracker,
    TextUnit,
    rows_to_markdown,
)

_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold text
# Up to this many pages, table detection also uses PyMuPDF's ML layout model (finds borderless
# tables, ~0.2 s per page). Longer PDFs use the fast vector-line detector so large documents
# finish well inside the ingestion timeout.
LAYOUT_MAX_PAGES = 40


def _find_tables(page: pymupdf.Page, use_layout: bool) -> list:
    """Best-effort table detection; pages without vector drawings cannot have ruled tables."""
    try:
        if not use_layout and not page.get_cdrawings():
            return []
        return page.find_tables(use_layout=use_layout).tables
    except Exception:  # table detection is best-effort
        return []


def _cell_texts(page: pymupdf.Page, table) -> list[list[str]]:  # noqa: ANN001
    """Text of every cell, from one word list per page (one clip per cell re-parses the page)."""
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            if not cell:
                cells.append("")
                continue
            x0, y0, x1, y1 = cell
            inside = [w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
            inside.sort(key=lambda w: (w[5], w[6], w[7]))
            lines: list[str] = []
            last = None
            for w in inside:
                key = (w[5], w[6])
                if key != last:
                    lines.append(w[4])
                    last = key
                else:
                    lines[-1] += " " + w[4]
            cells.append("\n".join(lines))
        rows.append(cells)
    return rows


def _overlaps(block_bbox: tuple, rects: list[pymupdf.Rect]) -> bool:
    """Return True when over half of a text block lies inside a table rectangle."""
    b = pymupdf.Rect(block_bbox)
    if b.is_empty:
        return False
    return any((b & r).get_area() > 0.5 * b.get_area() for r in rects)


def _body_font_size(doc: pymupdf.Document) -> float:
    """Median span size over the first pages: the 'normal' text size."""
    sizes = [
        span["size"]
        for page in doc.pages(0, min(doc.page_count, 10))
        for block in page.get_text("dict")["blocks"]
        if block["type"] == 0
        for line in block["lines"]
        for span in line["spans"]
        if span["text"].strip()
    ]
    return statistics.median(sizes) if sizes else 11.0


def _heading_level(line: dict, body: float, block_lines: int) -> int | None:
    """Return 1 or 2 if a line looks like a heading, else None."""
    text = "".join(s["text"] for s in line["spans"]).strip()
    if not text or len(text) > 100 or text.endswith((".", ",", ";", ":")):
        return None
    size = max(s["size"] for s in line["spans"])
    bold = all(s["flags"] & _BOLD_FLAG for s in line["spans"] if s["text"].strip())
    if size >= body * 1.4:
        return 1
    if size >= body * 1.15 or (bold and block_lines == 1 and size >= body):
        return 2
    return None


def extract_pdf(data: bytes, max_pages: int) -> ExtractedDocument:
    """Extract ordered text and table units from a PDF.

    Raises:
        CorruptFileError: unreadable or password-protected PDF.
        FileTooLargeError: more pages than ``max_pages``.

    """
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several internal error types
        raise CorruptFileError("The PDF file is corrupt or unreadable.") from exc
    if doc.needs_pass:
        raise CorruptFileError("Password-protected PDFs are not supported.")
    if doc.page_count > max_pages:
        raise FileTooLargeError(
            f"PDF has {doc.page_count} pages; the limit is {max_pages}.",
            {"page_count": doc.page_count, "limit": max_pages},
        )

    body = _body_font_size(doc)
    sections = SectionTracker()
    units: list[TextUnit] = []
    empty_pages: list[int] = []

    use_layout = doc.page_count <= LAYOUT_MAX_PAGES
    for page in doc:
        page_no = page.number + 1
        items: list[tuple[float, str, str, int]] = []  # (y, kind, text, heading_level)

        tables = _find_tables(page, use_layout)
        table_rects = [pymupdf.Rect(t.bbox) for t in tables]
        for t in tables:
            # Read cells from the page's words: Table.extract() can drop word spaces.
            md = rows_to_markdown(_cell_texts(page, t))
            if md:
                items.append((t.bbox[1], "table", md, 0))

        for block in page.get_text("dict", sort=True)["blocks"]:
            if block["type"] != 0 or _overlaps(block["bbox"], table_rects):
                continue
            paragraph: list[str] = []
            for line in block["lines"]:
                level = _heading_level(line, body, len(block["lines"]))
                text = "".join(s["text"] for s in line["spans"]).strip()
                if level:
                    if paragraph:
                        items.append((line["bbox"][1] - 0.1, "text", "\n".join(paragraph), 0))
                        paragraph = []
                    items.append((line["bbox"][1], "heading", text, level))
                elif text:
                    paragraph.append(text)
            if paragraph:
                items.append((block["bbox"][1], "text", "\n".join(paragraph), 0))

        items.sort(key=lambda it: it[0])
        page_has_text = False
        for _, kind, text, level in items:
            if kind == "heading":
                sections.push(level, text)
                page_has_text = True
                continue
            units.append(TextUnit(text=text, page=page_no, section=sections.path, kind=kind))  # type: ignore[arg-type]
            page_has_text = True
        if not page_has_text:
            empty_pages.append(page_no)

    return ExtractedDocument(units=units, page_count=doc.page_count, empty_pages=empty_pages)
