import pymupdf
import pytest

from app.errors import CorruptFileError, FileTooLargeError
from app.ingestion.cleaning import clean_units
from app.ingestion.extractors.office import extract_docx, extract_pptx
from app.ingestion.extractors.pdf import extract_pdf
from app.ingestion.extractors.text import extract_text


def test_pdf_pages_sections_and_table(samples):
    doc = extract_pdf(samples["policy_pdf"].read_bytes(), max_pages=300)
    assert doc.page_count == 3
    units = clean_units(doc.units, doc.page_count)
    sick = next(u for u in units if "10 paid sick days" in u.text)
    assert sick.page == 2 and sick.section == "2. Sick Leave"
    table = next(u for u in units if u.kind == "table")
    assert table.page == 2
    assert "| Leave type | Days per year | Carry over |" in table.text
    assert "| Sick | 10 | No |" in table.text


def test_pdf_header_footer_removed_and_hyphen_joined(samples):
    doc = extract_pdf(samples["policy_pdf"].read_bytes(), max_pages=300)
    text = " ".join(u.text for u in clean_units(doc.units, doc.page_count))
    assert "Employee Handbook 2026" not in text  # running header on every page
    assert "Page 2 of 3" not in text  # footer page numbers
    assert "HR portal" in text  # "por-\ntal" joined


def test_scanned_pdf_has_no_text(samples):
    doc = extract_pdf(samples["scanned_pdf"].read_bytes(), max_pages=300)
    assert doc.units == [] and doc.empty_pages == [1]


def test_pdf_page_limit(samples):
    with pytest.raises(FileTooLargeError):
        extract_pdf(samples["policy_pdf"].read_bytes(), max_pages=2)


def test_encrypted_pdf_rejected():
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "secret")
    data = d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(CorruptFileError):
        extract_pdf(data, max_pages=300)


def test_corrupt_pdf_rejected():
    with pytest.raises(CorruptFileError):
        extract_pdf(b"%PDF-1.7\nthis is not really a pdf", max_pages=300)


def test_docx_sections_and_table(samples):
    doc = extract_docx(samples["handbook_docx"].read_bytes())
    mfa = next(u for u in doc.units if u.text.startswith("MFA"))
    assert mfa.section == "IT Security Handbook > Passwords > Multi-factor authentication"
    assert mfa.page is None
    table = next(u for u in doc.units if u.kind == "table")
    assert table.section == "IT Security Handbook > Devices"
    assert "| Laptop | BitLocker |" in table.text


def test_pptx_slides_as_pages(samples):
    doc = extract_pptx(samples["slides_pptx"].read_bytes(), max_pages=300)
    assert doc.page_count == 2
    benefits = [u for u in doc.units if u.page == 2]
    assert {u.section for u in benefits} == {"Benefits"}
    assert any("Dental cover" in u.text for u in benefits)


def test_markdown_headings_become_sections(samples):
    doc = extract_text(samples["notes_md"].read_bytes(), markdown=True)
    assert [(u.section, u.text[:8]) for u in doc.units] == [
        ("FAQ > Payroll", "Salaries"),
        ("FAQ > Expenses", "Submit e"),
    ]


def test_urdu_text_preserved(samples):
    doc = extract_text(samples["urdu_txt"].read_bytes(), markdown=False)
    assert len(doc.units) == 2
    assert "دفتر جمعہ کو" in clean_units(doc.units, None)[0].text


def test_utf16_text_is_decoded():
    data = "Héllo wörld — café".encode("utf-16")
    assert extract_text(data, markdown=False).units[0].text == "Héllo wörld — café"
