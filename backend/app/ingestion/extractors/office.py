"""DOCX and PPTX extraction.

DOCX has no fixed pages, so its location metadata is the *section*: the
heading path built from Word's Heading styles. DOCX is read by
``app.ingestion.extractors.word`` (re-exported here). PPTX uses the slide
number as the "page".
"""

import io

from pptx import Presentation

from app.errors import CorruptFileError, FileTooLargeError
from app.ingestion.extractors.base import ExtractedDocument, TextUnit, rows_to_markdown
from app.ingestion.extractors.word import extract_docx

__all__ = ["extract_docx", "extract_pptx"]


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
        title = title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        section = title or f"Slide {number}"
        found = False
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                units.append(TextUnit(text=shape.text_frame.text.strip(), page=number, section=section))
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
