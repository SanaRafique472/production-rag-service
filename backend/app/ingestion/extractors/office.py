"""DOCX and PPTX extraction.

DOCX has no fixed pages, so its location metadata is the *section*: the
heading path built from Word's Heading styles. Paragraphs and tables are read
in their real document order. PPTX uses the slide number as the "page".
"""

import io
import re

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pptx import Presentation

from app.errors import CorruptFileError, FileTooLargeError
from app.ingestion.extractors.base import (
    ExtractedDocument,
    SectionTracker,
    TextUnit,
    rows_to_markdown,
)


def _heading_level(style_name: str) -> int | None:
    """Map Word style names ('Title', 'Heading 2') to a heading level."""
    if style_name == "Title":
        return 1
    match = re.match(r"Heading (\d)", style_name)
    return int(match.group(1)) + 1 if match else None  # Title sits above Heading 1


def extract_docx(data: bytes) -> ExtractedDocument:
    """Extract paragraphs and tables from a DOCX in reading order."""
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise CorruptFileError("The DOCX file is corrupt or unreadable.") from exc

    sections = SectionTracker()
    units: list[TextUnit] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            level = _heading_level(para.style.name if para.style is not None else "")
            if level:
                sections.push(level, text)
            else:
                units.append(TextUnit(text=text, section=sections.path))
        elif tag == "tbl":
            table = Table(child, doc)
            rows: list[list[str | None]] = []
            for row in table.rows:
                cells: list[str | None] = []
                for cell in row.cells:  # merged cells repeat; drop consecutive repeats
                    if not cells or cell.text != cells[-1]:
                        cells.append(cell.text)
                rows.append(cells)
            md = rows_to_markdown(rows)
            if md:
                units.append(TextUnit(text=md, section=sections.path, kind="table"))
    return ExtractedDocument(units=units, page_count=None)


def extract_pptx(data: bytes, max_pages: int) -> ExtractedDocument:
    """Extract text and tables per slide; the slide title is the section."""
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise CorruptFileError("The PPTX file is corrupt or unreadable.") from exc
    slide_count = len(prs.slides)
    if slide_count > max_pages:
        raise FileTooLargeError(f"Presentation has {slide_count} slides; the limit is {max_pages}.")

    units: list[TextUnit] = []
    empty: list[int] = []
    for number, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        title = (
            title_shape.text.strip()
            if title_shape is not None and title_shape.has_text_frame
            else ""
        )
        section = title or f"Slide {number}"
        found = False
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                units.append(
                    TextUnit(text=shape.text_frame.text.strip(), page=number, section=section)
                )
                found = True
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text for c in r.cells] for r in shape.table.rows]
                md = rows_to_markdown(rows)
                if md:
                    units.append(TextUnit(text=md, page=number, section=section, kind="table"))
                    found = True
        if title and not found:
            units.append(TextUnit(text=title, page=number, section=section))
            found = True
        if not found:
            empty.append(number)
    return ExtractedDocument(units=units, page_count=slide_count, empty_pages=empty)
