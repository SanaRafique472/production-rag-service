"""Legacy Word (.doc, Word 97-2003) and RTF extraction.

Two readers, chosen by ``settings.doc_converter``:

* **LibreOffice** (``auto`` when ``soffice`` is installed, or ``libreoffice``):
  converts the file to DOCX in an isolated temporary profile, then the normal
  DOCX reader runs, so headings, lists and tables are kept exactly.
* **Built-in** (always available, pure Python): reads the Word 97 binary
  format directly (FIB -> piece table -> text, per [MS-DOC]). It keeps the
  main text, footnotes, endnotes and text boxes, field *results* (not field
  codes) and rebuilds tables from the cell marks. Heading styles are not
  resolved, so sections are not available on this path.

RTF uses LibreOffice when available, otherwise ``striprtf``.
"""

import io
import logging
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import olefile
from striprtf.striprtf import rtf_to_text

from app.config import Settings
from app.errors import CorruptFileError, EncryptedFileError
from app.ingestion.extractors.base import ExtractedDocument, TextUnit, rows_to_markdown
from app.ingestion.extractors.word import extract_docx

log = logging.getLogger(__name__)

_CELL = "\x07"
_FIELD_BEGIN, _FIELD_SEP, _FIELD_END = "\x13", "\x14", "\x15"
# Characters with a special meaning in Word's text stream.
_TRANSLATE = str.maketrans(
    {
        "\x0b": "\n",  # vertical tab = manual line break
        "\x0c": "\r",  # page / section break ends a paragraph
        "\x0e": "\r",  # column break
        "\x1e": "-",  # non-breaking hyphen
        "\x1f": "",  # soft hyphen
        "\xa0": " ",
        "\x01": "",  # picture
        "\x02": "",  # auto-numbered footnote reference
        "\x03": "",  # footnote separator
        "\x04": "",  # footnote continuation
        "\x05": "",  # annotation reference
        "\x08": "",  # drawn object
    }
)


def _soffice(settings: Settings) -> str | None:
    if settings.doc_converter == "builtin":
        return None
    path = shutil.which("soffice") or shutil.which("libreoffice")
    if path is None and settings.doc_converter == "libreoffice":
        log.warning("DOC_CONVERTER=libreoffice but soffice is not installed; using the built-in reader")
    return path


def convert_with_libreoffice(data: bytes, suffix: str, settings: Settings) -> bytes | None:
    """Convert a .doc/.rtf to DOCX with LibreOffice; None if unavailable or the conversion failed."""
    soffice = _soffice(settings)
    if soffice is None:
        return None
    with tempfile.TemporaryDirectory(prefix="docconv-") as tmp:
        src = Path(tmp) / f"input{suffix}"
        src.write_bytes(data)
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation={(Path(tmp) / 'profile').as_uri()}",  # isolated, throw-away profile
            "--convert-to",
            "docx:MS Word 2007 XML",
            "--outdir",
            tmp,
            str(src),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=settings.libreoffice_timeout_s, check=False)  # noqa: S603
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("LibreOffice conversion failed; using the built-in reader", extra={"error_type": str(exc)[:80]})
            return None
        out = Path(tmp) / "input.docx"
        return out.read_bytes() if out.exists() and out.stat().st_size else None


# ---------------------------------------------------------------- Word 97 binary


def _strip_fields(text: str) -> str:
    """Keep field results and drop field codes (begin 0x13, code, separator 0x14, result, end 0x15)."""
    out: list[str] = []
    stack: list[bool] = []  # True while inside the code part of a field
    for ch in text:
        if ch == _FIELD_BEGIN:
            stack.append(True)
        elif ch == _FIELD_SEP:
            if stack:
                stack[-1] = False
        elif ch == _FIELD_END:
            if stack:
                stack.pop()
        elif not any(stack):
            out.append(ch)
    return "".join(out)


def _piece_text(word: bytes, table: bytes, fc_clx: int, lcb_clx: int) -> str:
    """Rebuild the document's character stream from the piece table (CLX)."""
    clx = table[fc_clx : fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:  # Prc entries (formatting) come first
        (cb,) = struct.unpack_from("<h", clx, i + 1)
        i += 3 + cb
    if i >= len(clx) or clx[i] != 0x02:
        raise CorruptFileError("The DOC file is corrupt (no piece table).")
    (lcb,) = struct.unpack_from("<I", clx, i + 1)
    plc = clx[i + 5 : i + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return ""
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    parts: list[str] = []
    for k in range(n):
        (fc_value,) = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)
        count = cps[k + 1] - cps[k]
        if count <= 0:
            continue
        if fc_value & 0x40000000:  # compressed: 8-bit cp1252
            start = (fc_value & 0x3FFFFFFF) // 2
            parts.append(word[start : start + count].decode("cp1252", errors="replace"))
        else:  # UTF-16LE
            start = fc_value & 0x3FFFFFFF
            parts.append(word[start : start + 2 * count].decode("utf-16-le", errors="replace"))
    return "".join(parts)


def _clean(s: str) -> str:
    return "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32).strip()


def _blocks_to_units(text: str, section: str | None) -> list[TextUnit]:
    """Split a Word text stream into paragraphs and tables.

    Paragraphs end with CR (0x0D). In a table every cell ends with 0x07 and every
    row ends with one more 0x07 (the row mark). Everything between the first
    cell of a row and its row mark belongs to that row, so multi-paragraph cells
    stay together. (Without paragraph properties, extra paragraphs at the very
    start of a row's first cell are read as body text.)
    """
    tokens: list[tuple[str, str]] = []
    segment = ""
    for ch in text:
        if ch in ("\r", _CELL):
            tokens.append((_clean(segment), ch))
            segment = ""
        else:
            segment += ch
    if _clean(segment):
        tokens.append((_clean(segment), "\r"))

    units: list[TextUnit] = []
    rows: list[list[str]] = []
    row: list[str] = []
    cell: list[str] = []
    in_row = after_cell = False

    def flush_table() -> None:
        nonlocal rows
        md = rows_to_markdown(rows)  # type: ignore[arg-type]
        if md:
            units.append(TextUnit(text=md, section=section, kind="table"))
        rows = []

    for value, term in tokens:
        if term == _CELL:
            if after_cell and not value and not cell:  # row mark
                rows.append(row)
                row, in_row, after_cell = [], False, False
                continue
            row.append(" ".join([*cell, value]).strip())
            cell, in_row, after_cell = [], True, True
            continue
        if in_row:  # paragraph inside the next cell of this row
            if value:
                cell.append(value)
            after_cell = False
            continue
        if rows:
            flush_table()
        if value:
            units.append(TextUnit(text=value, section=section))
    if row or cell:
        rows.append([*row, *cell])
    if rows:
        flush_table()
    return units


def extract_word97(data: bytes) -> ExtractedDocument:
    """Pure-Python reader for Word 97-2003 binary documents."""
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            word = ole.openstream("WordDocument").read()
            (ident,) = struct.unpack_from("<H", word, 0)
            if ident != 0xA5EC:
                raise CorruptFileError("The DOC file is corrupt (unknown format version).")
            (flags,) = struct.unpack_from("<H", word, 0x0A)
            if flags & 0x0100:
                raise EncryptedFileError(
                    "This DOC file is password-protected. Remove the password and upload it again."
                )
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if not ole.exists(table_name):
                raise CorruptFileError("The DOC file is corrupt (missing table stream).")
            table = ole.openstream(table_name).read()
    except (OSError, struct.error) as exc:
        raise CorruptFileError("The DOC file is corrupt or unreadable.") from exc

    try:
        pos = 32
        (csw,) = struct.unpack_from("<H", word, pos)
        pos += 2 + 2 * csw
        (cslw,) = struct.unpack_from("<H", word, pos)
        lw = pos + 2
        ccp = list(struct.unpack_from(f"<{min(cslw, 22)}i", word, lw))
        pos = lw + 4 * cslw
        (cb_fclcb,) = struct.unpack_from("<H", word, pos)
        if cb_fclcb < 34:
            raise CorruptFileError("The DOC file uses an unsupported (pre-97) Word format.")
        fc_clx, lcb_clx = struct.unpack_from("<iI", word, pos + 2 + 33 * 8)
        full = _piece_text(word, table, fc_clx, lcb_clx)
    except struct.error as exc:
        raise CorruptFileError("The DOC file is corrupt or unreadable.") from exc

    ccp += [0] * (11 - len(ccp))
    lengths = {"main": ccp[3], "ftn": ccp[4], "hdd": ccp[5], "atn": ccp[7], "edn": ccp[8], "txbx": ccp[9]}
    offset, ranges = 0, {}
    for name in ("main", "ftn", "hdd", "atn", "edn", "txbx"):
        ranges[name] = full[offset : offset + max(lengths[name], 0)]
        offset += max(lengths[name], 0)
    if not lengths["main"]:  # damaged FIB counts: take everything
        ranges = {"main": full}

    units: list[TextUnit] = []
    for name, section in (("main", None), ("txbx", None), ("ftn", "Footnotes"), ("edn", "Endnotes")):
        part = ranges.get(name, "")
        if part:
            units += _blocks_to_units(_strip_fields(part).translate(_TRANSLATE), section)
    return ExtractedDocument(units=units, page_count=None)


# ---------------------------------------------------------------- entry points


def extract_doc(data: bytes, settings: Settings) -> ExtractedDocument:
    """Extract a legacy .doc (or an RTF file that carries a .doc name)."""
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{\\rtf"):
        return extract_rtf(data, settings)
    builtin = extract_word97(data)
    converted = convert_with_libreoffice(data, ".doc", settings)
    if converted is None:
        return builtin
    try:
        via_lo = extract_docx(converted)
    except CorruptFileError:
        log.warning("LibreOffice output unreadable; using the built-in DOC reader")
        return builtin
    return via_lo if _richer_or_equal(via_lo, builtin) else builtin


def _richer_or_equal(a: ExtractedDocument, b: ExtractedDocument) -> bool:
    """Prefer LibreOffice (headings, lists) unless it lost tables or a noticeable amount of text."""
    tables = lambda d: sum(u.kind == "table" for u in d.units)  # noqa: E731
    chars = lambda d: sum(len("".join(u.text.split())) for u in d.units)  # noqa: E731
    return tables(a) >= tables(b) and chars(a) >= 0.9 * chars(b)


def extract_rtf(data: bytes, settings: Settings) -> ExtractedDocument:
    """Extract an RTF document (LibreOffice when available, else ``striprtf``)."""
    converted = convert_with_libreoffice(data, ".rtf", settings)
    if converted is not None:
        try:
            return extract_docx(converted)
        except CorruptFileError:
            pass
    try:
        text = rtf_to_text(data.decode("latin-1"), errors="ignore")
    except Exception as exc:
        raise CorruptFileError("The RTF file is corrupt or unreadable.") from exc
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n")]
    return ExtractedDocument(units=[TextUnit(text=p) for p in paragraphs if p], page_count=None)
