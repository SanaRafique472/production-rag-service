"""Regression tests for Word uploads (DOCX, legacy DOC, RTF).

Root causes covered:
* the zip-bomb guard rejected ordinary DOCX files whose parts compress very
  well (blank BMP images, tables of empty cells) -> uploads failed with 400;
* ``.doc`` was not accepted at all, and renamed files (.doc <-> .docx) were 415;
* the old DOCX reader silently dropped content controls, tracked insertions,
  text boxes, field results, list numbers, footnotes and repeated cell values.
"""

import io
import uuid
import zipfile

import pytest

from app.errors import CorruptFileError, EncryptedFileError, UnsupportedFileTypeError
from app.ingestion.extractors.legacy_doc import extract_doc, extract_word97
from app.ingestion.extractors.office import extract_docx
from app.ingestion.pipeline import process_file
from app.ingestion.validation import FileType, detect_file_type

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT = "http://purl.oclc.org/ooxml/wordprocessingml/main"


def _package(document_xml: str, parts: dict[str, str] | None = None, main: str = "word/document.xml") -> bytes:
    """Build a minimal DOCX package by hand (lets tests control every XML detail)."""
    parts = parts or {}
    rels = "".join(
        f'<Relationship Id="r{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" '
        f'Target="{name.split("/")[-1]}"/>'
        for i, (name, kind) in enumerate((n, n.split("/")[-1].removesuffix(".xml").rstrip("0123456789")) for n in parts)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{main}" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            f'officeDocument" Target="{main}"/></Relationships>',
        )
        folder, name = main.rsplit("/", 1)
        zf.writestr(
            f"{folder}/_rels/{name}.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        zf.writestr(main, document_xml)
        for part, xml in parts.items():
            zf.writestr(part, xml)
    return buf.getvalue()


def _doc(body: str, ns: str = W) -> str:
    return f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'


def _p(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


# ---------------------------------------------------------------- validation


def test_docx_with_highly_compressible_image_is_accepted(samples, settings):
    data = samples["blank_image_docx"].read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        worst = max(i.file_size / i.compress_size for i in zf.infolist() if i.compress_size)
    assert worst > 200  # the old per-member ratio guard (100) rejected this ordinary file
    assert detect_file_type("site_inspection.docx", data, settings) is FileType.DOCX
    result = process_file("site_inspection.docx", data, document_id=str(uuid.uuid4()), settings=settings)
    assert "emergency lights" in " ".join(c.text for c in result.chunks)


def test_docx_with_empty_table_grid_is_accepted(settings):
    rows = "".join("<w:tr>" + "<w:tc><w:p/></w:tc>" * 20 + "</w:tr>" for _ in range(600))
    data = _package(_doc(_p("Overtime above 45 hours needs approval.") + f"<w:tbl>{rows}</w:tbl>"))
    assert detect_file_type("timesheet.docx", data, settings) is FileType.DOCX


def test_zip_bomb_rejected_by_expanded_size(settings):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/padding.xml", "A" * 5_000_000)
    tight = settings.model_copy(update={"max_zip_uncompressed_mb": 4})
    with pytest.raises(CorruptFileError, match="too large"):
        detect_file_type("bomb.docx", buf.getvalue(), tight)


def test_zip_bomb_rejected_by_ratio_on_large_member(settings):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/padding.xml", "A" * 5_000_000)  # ~1000:1
    strict = settings.model_copy(update={"zip_ratio_min_member_mb": 1})
    with pytest.raises(CorruptFileError, match="zip bomb"):
        detect_file_type("bomb.docx", buf.getvalue(), strict)


def test_legacy_doc_detected(samples, settings):
    assert detect_file_type("policy.doc", samples["legacy_doc"].read_bytes(), settings) is FileType.DOC


def test_renamed_word_files_detected_by_content(samples, settings):
    assert detect_file_type("policy.docx", samples["legacy_doc"].read_bytes(), settings) is FileType.DOC
    assert detect_file_type("handbook.doc", samples["handbook_docx"].read_bytes(), settings) is FileType.DOCX
    assert detect_file_type("note.doc", b"{\\rtf1\\ansi Refunds within 30 days.\\par}", settings) is FileType.RTF


def test_non_word_content_with_word_extension_rejected(samples, settings):
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("x.doc", b"<html><body>saved web page</body></html>", settings)
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("x.docx", samples["slides_pptx"].read_bytes(), settings)


def _set_encrypted_flag(doc: bytes) -> bytes:
    """Set fEncrypted in the FIB of the WordDocument stream (starts with 0xA5EC on a sector boundary)."""
    data = bytearray(doc)
    for offset in range(512, len(data), 512):
        if data[offset : offset + 2] == b"\xec\xa5":
            data[offset + 0x0B] |= 0x01  # high byte of the little-endian flags word: bit 0x0100
            return bytes(data)
    raise AssertionError("FIB not found")


def _rename_ole_stream(doc: bytes, old: str, new: str) -> bytes:
    """Rename a directory entry in a compound file (for building an 'encrypted package' sample)."""
    data = bytearray(doc)
    old_name = old.encode("utf-16-le")
    i = data.index(old_name)
    entry = new.encode("utf-16-le") + b"\x00\x00"
    data[i : i + 64] = entry.ljust(64, b"\x00")
    data[i + 64 : i + 66] = len(entry).to_bytes(2, "little")
    return bytes(data)


def test_password_protected_word_files_get_clear_error(samples, settings):
    doc = samples["legacy_doc"].read_bytes()
    with pytest.raises(EncryptedFileError, match="password"):
        detect_file_type("locked.doc", _set_encrypted_flag(doc), settings)
    # An encrypted DOCX is an OLE container with EncryptionInfo/EncryptedPackage streams.
    with pytest.raises(EncryptedFileError, match="password"):
        detect_file_type("locked.docx", _rename_ole_stream(doc, "WordDocument", "EncryptionInfo"), settings)


# ---------------------------------------------------------------- DOCX content


def test_docx_reads_controls_tracked_changes_fields_textboxes_lists(samples):
    units = extract_docx(samples["features_docx"].read_bytes()).units
    texts = [u.text for u in units]
    assert "The client is Northwind Traders Ltd, registered in Lahore." in texts  # content control
    assert "Termination notice: sixty days in writing." in texts  # insertion kept, deletion dropped
    assert "Contract value: PKR 4,500,000" in texts  # field result kept, field code dropped
    assert texts.count("Governing law is the law of Pakistan.") == 1  # text box read once
    assert ["1. Send the invoice.", "2. Wait for approval.", "3. Receive payment."] == texts[4:7]
    assert "- Bring your ID card." in texts
    assert all("DOCPROPERTY" not in t and "thirty" not in t for t in texts)


def test_docx_merged_cells_keep_columns_and_repeated_values(samples):
    table = next(u for u in extract_docx(samples["features_docx"].read_bytes()).units if u.kind == "table")
    assert table.section == "Service Contract > Payment steps"
    assert "| North | Licences | 5 | 5 |" in table.text  # equal neighbours are not de-duplicated
    assert "| North | Support | Included |  |" in table.text  # vertical merge repeated, span aligned


def test_docx_footnotes_headers_and_nested_tables():
    nested = "<w:tbl><w:tr><w:tc>" + _p("inner a") + "</w:tc><w:tc>" + _p("inner b") + "</w:tc></w:tr></w:tbl>"
    body = (
        _p("Report", "Title")
        + "<w:p><w:r><w:t>See note</w:t></w:r><w:r><w:footnoteReference w:id='1'/></w:r></w:p>"
        + "<w:tbl><w:tr><w:tc>"
        + _p("Outer")
        + "</w:tc><w:tc>"
        + nested
        + "</w:tc></w:tr><w:tr><w:tc>"
        + _p("x")
        + "</w:tc><w:tc>"
        + _p("y")
        + "</w:tc></w:tr></w:tbl>"
    )
    parts = {
        "word/footnotes.xml": f'<w:footnotes xmlns:w="{W}"><w:footnote w:type="separator" w:id="-1">'
        f'<w:p><w:r><w:t>SEPARATOR</w:t></w:r></w:p></w:footnote><w:footnote w:id="1">{_p("Figures are unaudited.")}'
        "</w:footnote></w:footnotes>",
        "word/header1.xml": f'<w:hdr xmlns:w="{W}">{_p("ACME Confidential")}</w:hdr>',
        "word/footer1.xml": f'<w:ftr xmlns:w="{W}">{_p("Page 1 of 3")}</w:ftr>',
    }
    units = extract_docx(_package(_doc(body), parts)).units
    texts = [u.text for u in units]
    assert "| Outer | inner a / inner b |" in next(u.text for u in units if u.kind == "table")
    assert "[1] Figures are unaudited." in texts and "SEPARATOR" not in " ".join(texts)
    assert "ACME Confidential" in texts


def test_docx_strict_ooxml_and_custom_main_part_name():
    data = _package(_doc(_p("Policy", "Heading1") + _p("Strict namespace text."), STRICT), main="word/document2.xml")
    units = extract_docx(data).units
    assert [(u.section, u.text) for u in units] == [("Policy", "Strict namespace text.")]


def test_docx_outline_level_headings():
    body = '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Scope</w:t></w:r></w:p>' + _p(
        "Applies to all staff."
    )
    assert extract_docx(_package(_doc(body))).units[0].section == "Scope"


def test_corrupt_docx_xml_rejected():
    with pytest.raises(CorruptFileError):
        extract_docx(_package("<w:document><w:body><w:p>"))


# ---------------------------------------------------------------- DOC / RTF content


def test_builtin_doc_reader_extracts_text_and_table(samples):
    doc = extract_word97(samples["legacy_doc"].read_bytes())
    texts = [u.text for u in doc.units]
    assert "Employees receive 24 days of paid annual leave per year." in texts
    assert "Unused days expire on 31 March." in texts
    table = next(u for u in doc.units if u.kind == "table")
    assert "| Grade | Annual days | Carry-over |" in table.text and "| G2 | 24 | 8 |" in table.text


def test_doc_pipeline_without_libreoffice(samples, settings):
    builtin = settings.model_copy(update={"doc_converter": "builtin"})
    result = process_file(
        "leave.doc", samples["legacy_doc"].read_bytes(), document_id=str(uuid.uuid4()), settings=builtin
    )
    assert result.file_type is FileType.DOC
    assert any("24 days of paid annual leave" in c.text for c in result.chunks)


def test_rtf_named_doc_extracted(settings):
    builtin = settings.model_copy(update={"doc_converter": "builtin"})
    rtf = b"{\\rtf1\\ansi\\pard Refund requests must be filed within 30 days of purchase.\\par}"
    units = extract_doc(rtf, builtin).units
    assert units[0].text == "Refund requests must be filed within 30 days of purchase."


def test_large_docx_is_processed(settings):
    paragraphs = "".join(
        _p(f"Section {i} explains procedure {i} for warehouse shipments and invoices in detail.") for i in range(6000)
    )
    rows = "".join(f"<w:tr><w:tc>{_p(f'BR{i:04d}')}</w:tc><w:tc>{_p('Lahore')}</w:tc></w:tr>" for i in range(2000))
    data = _package(
        _doc(_p("Manual", "Title") + paragraphs + f"<w:tbl>{rows}</w:tbl>" + _p("Escalation code ZEBRA-42."))
    )
    result = process_file("manual.docx", data, document_id=str(uuid.uuid4()), settings=settings)
    joined = " ".join(c.text for c in result.chunks)
    assert len(result.chunks) > 100 and "ZEBRA-42" in joined and "BR1999" in joined
