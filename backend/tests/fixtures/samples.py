"""Generate small, realistic sample documents for tests and notebooks.

Each file targets something the ingestion pipeline must handle: headings,
tables, repeated headers/footers, hyphenated line breaks, Urdu text, a
scanned (image-only) PDF, an empty file and a disguised executable.

Word-specific samples reproduce what real Word files contain and what used to
break uploads or lose text: content controls, tracked changes, text boxes,
fields, numbered lists, merged cells, and highly compressible parts (a blank
BMP image) that tripped the old zip-bomb ratio check. ``leave_policy_legacy.doc``
is a real Word 97-2003 file written by LibreOffice (kept as a binary fixture).
"""

import io
import shutil
from pathlib import Path

import pymupdf
from docx import Document
from docx.oxml import parse_xml
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

POLICY_PAGES = [
    (
        "1. Annual Leave",
        "Full-time employees receive 20 days of paid annual leave per calendar year. "
        "Leave must be requested at least two weeks in advance through the HR por-\n"
        "tal. Unused leave of up to five days may be carried over to the next year.",
    ),
    (
        "2. Sick Leave",
        "Employees are entitled to 10 paid sick days per year. A medical certificate "
        "is required for any absence longer than three consecutive days. Sick leave "
        "cannot be carried over.",
    ),
    (
        "3. Remote Work",
        "Staff may work remotely up to two days per week with manager approval. "
        "Remote work is not available during the first three months of employment.",
    ),
]

LEAVE_TABLE = [
    ["Leave type", "Days per year", "Carry over"],
    ["Annual", "20", "Up to 5"],
    ["Sick", "10", "No"],
    ["Parental", "90", "No"],
]


def _make_policy_pdf(path: Path) -> None:
    """Three-page PDF with headings, a table, a running header and page footers."""
    doc = pymupdf.open()
    for i, (heading, body) in enumerate(POLICY_PAGES, start=1):
        page = doc.new_page()
        page.insert_text((72, 40), "Acme Corp - Employee Handbook 2026", fontsize=8)
        page.insert_text((72, 90), heading, fontsize=16, fontname="hebo")
        page.insert_textbox(pymupdf.Rect(72, 110, 520, 300), body, fontsize=11)
        if i == 2:  # a real table on page 2
            x0, y0, cw, rh = 72, 320, 140, 24
            for r, row in enumerate(LEAVE_TABLE):
                for c, cell in enumerate(row):
                    rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
                    page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                    page.insert_text((rect.x0 + 4, rect.y1 - 7), cell, fontsize=10)
        page.insert_text((280, 800), f"Page {i} of {len(POLICY_PAGES)}", fontsize=8)
    doc.save(path)


def _make_scanned_pdf(path: Path) -> None:
    """PDF whose page is only an image of text (no text layer), like a scan."""
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((72, 100), "This page was scanned and has no text layer.", fontsize=14)
    pix = p.get_pixmap(dpi=72)
    out = pymupdf.open()
    page = out.new_page()
    page.insert_image(page.rect, pixmap=pix)
    out.save(path)


def _make_handbook_docx(path: Path) -> None:
    """DOCX with a title, nested headings, paragraphs and a table."""
    d = Document()
    d.add_heading("IT Security Handbook", level=0)
    d.add_heading("Passwords", level=1)
    d.add_paragraph(
        "Passwords must be at least 14 characters long and changed every 180 days. "
        "Password managers approved by IT may be used."
    )
    d.add_heading("Multi-factor authentication", level=2)
    d.add_paragraph("MFA is mandatory for email, VPN and the HR portal.")
    d.add_heading("Devices", level=1)
    d.add_paragraph("Company laptops must use full-disk encryption.")
    table = d.add_table(rows=3, cols=2)
    for r, row in enumerate([["Device", "Encryption"], ["Laptop", "BitLocker"], ["Phone", "Built-in"]]):
        for c, val in enumerate(row):
            table.cell(r, c).text = val
    d.save(path)


def _make_slides_pptx(path: Path) -> None:
    """Two-slide PPTX with titles and a bullet body."""
    prs = Presentation()
    for title, body in [
        ("Onboarding Week", "Day 1: laptop setup\nDay 2: security training"),
        ("Benefits", "Health insurance starts on the first day of employment."),
    ]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    tb = prs.slides[1].shapes.add_textbox(Inches(1), Inches(5), Inches(6), Inches(1))
    tb.text_frame.text = "Dental cover is optional."
    prs.save(path)


_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
_V = 'xmlns:v="urn:schemas-microsoft-com:vml"'
LEGACY_DOC = Path(__file__).parent / "files" / "leave_policy_legacy.doc"


def _make_word_features_docx(path: Path) -> None:
    """DOCX using the Word features python-docx's simple reading skipped."""
    d = Document()
    d.add_heading("Service Contract", level=0)
    body = d.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f"<w:sdt {_W}><w:sdtPr><w:alias w:val='Client'/></w:sdtPr><w:sdtContent>"
            "<w:p><w:r><w:t>The client is Northwind Traders Ltd, registered in Lahore.</w:t></w:r></w:p>"
            "</w:sdtContent></w:sdt>"
        ),
    )
    p = d.add_paragraph("Termination notice: ")
    p._p.append(parse_xml(f"<w:ins {_W} w:id='1' w:author='A'><w:r><w:t>sixty days in writing.</w:t></w:r></w:ins>"))
    p._p.append(parse_xml(f"<w:del {_W} w:id='2' w:author='A'><w:r><w:delText>thirty days.</w:delText></w:r></w:del>"))
    p = d.add_paragraph("Contract value: ")
    for run in (  # complex field: the code (instrText) must be dropped, the result kept
        "<w:fldChar w:fldCharType='begin'/>",
        "<w:instrText>DOCPROPERTY Value</w:instrText>",
        "<w:fldChar w:fldCharType='separate'/>",
        "<w:t>PKR 4,500,000</w:t>",
        "<w:fldChar w:fldCharType='end'/>",
    ):
        p._p.append(parse_xml(f"<w:r {_W}>{run}</w:r>"))
    p = d.add_paragraph()
    p._p.append(  # text box stored twice (modern Choice + VML Fallback): must be read once
        parse_xml(
            f"<w:r {_W}><mc:AlternateContent {_MC}><mc:Choice Requires='wps'><w:drawing>"
            "<w:txbxContent><w:p><w:r><w:t>Governing law is the law of Pakistan.</w:t></w:r></w:p></w:txbxContent>"
            f"</w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape {_V}><v:textbox><w:txbxContent><w:p><w:r>"
            "<w:t>Governing law is the law of Pakistan.</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape>"
            "</w:pict></mc:Fallback></mc:AlternateContent></w:r>"
        )
    )
    d.add_heading("Payment steps", level=1)
    for text in ["Send the invoice.", "Wait for approval.", "Receive payment."]:
        d.add_paragraph(text, style="List Number")
    d.add_paragraph("Bring your ID card.", style="List Bullet")
    t = d.add_table(rows=3, cols=4)
    rows = [["Region", "Item", "Q1", "Q2"], ["North", "Licences", "5", "5"], ["", "Support", "Included", ""]]
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            t.cell(i, j).text = value
    t.cell(2, 2).merge(t.cell(2, 3))  # horizontal merge: "Included" spans Q1-Q2
    t.cell(1, 0).merge(t.cell(2, 0))  # vertical merge: "North" spans both rows
    d.save(path)


def _make_blank_image_docx(path: Path) -> None:
    """A normal DOCX with a blank BMP image: that part compresses ~1000:1 (rejected before the fix)."""
    d = Document()
    d.add_heading("Site Inspection Report", level=0)
    d.add_paragraph("Inspection of the Gulberg branch was completed on 12 May 2026 by Ahmed Khan.")
    buf = io.BytesIO()
    Image.new("RGB", (1200, 1200), "white").save(buf, "BMP")
    buf.seek(0)
    d.add_picture(buf)
    d.add_paragraph("Two emergency lights failed and must be replaced by 30 June 2026.")
    d.save(path)


def build_samples(out_dir: Path) -> dict[str, Path]:
    """Write every sample file into ``out_dir`` and return their paths by name."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "policy_pdf": out_dir / "leave_policy.pdf",
        "scanned_pdf": out_dir / "scanned.pdf",
        "handbook_docx": out_dir / "it_security.docx",
        "slides_pptx": out_dir / "onboarding.pptx",
        "notes_md": out_dir / "faq.md",
        "urdu_txt": out_dir / "urdu_notice.txt",
        "empty_pdf": out_dir / "empty.pdf",
        "fake_pdf": out_dir / "invoice.pdf",
        "features_docx": out_dir / "service_contract.docx",
        "blank_image_docx": out_dir / "site_inspection.docx",
        "legacy_doc": out_dir / "leave_policy_legacy.doc",
    }
    _make_word_features_docx(paths["features_docx"])
    _make_blank_image_docx(paths["blank_image_docx"])
    shutil.copyfile(LEGACY_DOC, paths["legacy_doc"])
    _make_policy_pdf(paths["policy_pdf"])
    _make_scanned_pdf(paths["scanned_pdf"])
    _make_handbook_docx(paths["handbook_docx"])
    _make_slides_pptx(paths["slides_pptx"])
    paths["notes_md"].write_text(
        "# FAQ\n\n## Payroll\n\nSalaries are paid on the last working day of each month.\n\n"
        "## Expenses\n\nSubmit expense claims within 30 days.\n",
        encoding="utf-8",
    )
    paths["urdu_txt"].write_text(
        "دفتر جمعہ کو دوپہر ایک بجے بند ہو جائے گا۔\n\nتمام ملازمین اپنی حاضری درج کریں۔\n",
        encoding="utf-8",
    )
    paths["empty_pdf"].write_bytes(b"")
    paths["fake_pdf"].write_bytes(b"MZ\x90\x00" + b"\x00" * 200)  # a Windows .exe renamed to .pdf
    return paths


if __name__ == "__main__":
    for name, p in build_samples(Path(__file__).resolve().parents[3] / "data" / "samples").items():
        print(f"{name:14s} {p}")
