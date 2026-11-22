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

    for page in doc:
        page_no = page.number + 1
        items: list[tuple[float, str, str, int]] = []  # (y, kind, text, heading_level)

        try:
            tables = page.find_tables().tables
        except Exception:  # table detection is best-effort
            tables = []
        table_rects = [pymupdf.Rect(t.bbox) for t in tables]
        for t in tables:
            # Read each cell from its own rectangle: Table.extract() can drop word spaces.
            rows = [
                [page.get_text("text", clip=cell).strip() if cell else "" for cell in row.cells]
                for row in t.rows
            ]
            md = rows_to_markdown(rows)
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
