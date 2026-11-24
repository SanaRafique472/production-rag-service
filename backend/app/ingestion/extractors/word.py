"""DOCX extraction straight from the package XML.

python-docx's ``Paragraph.text`` / ``iterchildren`` only see plain paragraphs
and tables at the top of the body, so real Word files lost a lot of text:
content controls (``w:sdt``: forms, templates, cover pages), tracked
insertions (``w:ins``), text boxes and shapes, field results, footnotes,
headers and footers. Merged cells were de-duplicated by comparing text, which
also dropped legitimate repeated values ("5 | 5").

This reader walks the XML in document order and keeps:

* headings (Title / Heading N / outline level) as the section path;
* paragraphs, including text inside content controls, tracked insertions,
  hyperlinks, smart tags and field results (field codes are skipped,
  deleted text is skipped);
* list items with their real bullet or number ("1.", "a)", "-");
* tables as Markdown rows with correct column alignment for merged cells,
  nested tables flattened into their cell;
* text boxes / shapes (once, not twice for the VML fallback copy);
* footnotes, endnotes, headers and footers (de-duplicated).
"""

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from app.errors import CorruptFileError
from app.ingestion.extractors.base import ExtractedDocument, SectionTracker, TextUnit, rows_to_markdown
from app.ingestion.ooxml import attr, child, local, main_document_part, parse_xml, read_rels

# Elements whose content is never document text.
_SKIP = {
    "pPr",
    "rPr",
    "tblPr",
    "tblGrid",
    "trPr",
    "tcPr",
    "sectPr",
    "sdtPr",
    "sdtEndPr",
    "del",
    "moveFrom",
    "delText",
    "instrText",
    "delInstrText",
    "fldData",
    "commentReference",
    "footnoteReference",
    "endnoteReference",
    "annotationRef",
    "docPr",
    "cNvPr",
    "bookmarkStart",
    "bookmarkEnd",
    "proofErr",
    "lastRenderedPageBreak",
    "permStart",
    "permEnd",
}
# Containers that simply wrap block-level content.
_BLOCK_WRAPPERS = {"sdt", "sdtContent", "customXml", "ins", "moveTo", "smartTag", "body", "txbxContent"}
_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]  # fmt: skip


def _roman(n: int) -> str:
    out = ""
    for value, sym in _ROMAN:
        while n >= value:
            out, n = out + sym, n - value
    return out


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(97 + rem) + out
    return out


def _format_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter",):
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


@dataclass
class _Style:
    name: str = ""
    based_on: str | None = None
    outline: int | None = None
    num_id: str | None = None
    ilvl: int | None = None


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = "%1."
    start: int = 1


@dataclass
class _Numbering:
    abstract: dict[str, dict[int, _Level]] = field(default_factory=dict)
    nums: dict[str, tuple[str, dict[int, int]]] = field(default_factory=dict)  # numId -> (abstractId, overrides)
    counters: dict[str, list[int | None]] = field(default_factory=dict)

    def level(self, num_id: str, ilvl: int) -> _Level | None:
        entry = self.nums.get(num_id)
        if entry is None:
            return None
        return self.abstract.get(entry[0], {}).get(ilvl)

    def label(self, num_id: str, ilvl: int) -> str:
        """Advance the counter for this list level and render its label."""
        lvl = self.level(num_id, ilvl)
        if lvl is None:
            return "-"
        if lvl.fmt == "bullet":
            return "-"
        if lvl.fmt == "none":
            return ""
        counters = self.counters.setdefault(num_id, [None] * 9)
        overrides = self.nums[num_id][1]
        start = overrides.get(ilvl, lvl.start)
        counters[ilvl] = start if counters[ilvl] is None else counters[ilvl] + 1  # type: ignore[operator]
        for deeper in range(ilvl + 1, 9):
            counters[deeper] = None

        def repl(m: re.Match) -> str:
            k = int(m.group(1)) - 1
            ref = self.level(num_id, k) or _Level()
            value = counters[k] if 0 <= k < 9 and counters[k] is not None else overrides.get(k, ref.start)
            return _format_number(int(value), lvl.fmt if k == ilvl else ref.fmt)

        return re.sub(r"%(\d)", repl, lvl.text).strip()


def _alternate(el: etree._Element) -> etree._Element | None:
    """mc:AlternateContent: the first Choice, else the Fallback (never both: they hold the same content)."""
    for c in el:
        if local(c) == "Choice":
            return c
    return child(el, "Fallback")


def _int(value: str | None, default: int | None = None) -> int | None:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _load_styles(root: etree._Element | None) -> dict[str, _Style]:
    styles: dict[str, _Style] = {}
    if root is None:
        return styles
    for el in root:
        if local(el) != "style":
            continue
        ppr = child(el, "pPr")
        num_pr = child(ppr, "numPr")
        styles[attr(el, "styleId") or ""] = _Style(
            name=attr(child(el, "name"), "val") or "",
            based_on=attr(child(el, "basedOn"), "val"),
            outline=_int(attr(child(ppr, "outlineLvl"), "val")),
            num_id=attr(child(num_pr, "numId"), "val"),
            ilvl=_int(attr(child(num_pr, "ilvl"), "val")),
        )
    return styles


def _load_numbering(root: etree._Element | None) -> _Numbering:
    numbering = _Numbering()
    if root is None:
        return numbering
    for el in root:
        if local(el) == "abstractNum":
            levels: dict[int, _Level] = {}
            for lvl in el:
                if local(lvl) == "lvl":
                    levels[_int(attr(lvl, "ilvl"), 0) or 0] = _Level(
                        fmt=attr(child(lvl, "numFmt"), "val") or "decimal",
                        text=attr(child(lvl, "lvlText"), "val") or "",
                        start=_int(attr(child(lvl, "start"), "val"), 1) or 1,
                    )
            numbering.abstract[attr(el, "abstractNumId") or ""] = levels
        elif local(el) == "num":
            overrides = {}
            for ov in el:
                if local(ov) == "lvlOverride":
                    start = _int(attr(child(ov, "startOverride"), "val"))
                    if start is not None:
                        overrides[_int(attr(ov, "ilvl"), 0) or 0] = start
            numbering.nums[attr(el, "numId") or ""] = (attr(child(el, "abstractNumId"), "val") or "", overrides)
    return numbering


class _Reader:
    """Walks one DOCX package and produces ``TextUnit`` objects."""

    def __init__(self, zf: zipfile.ZipFile) -> None:
        self.zf = zf
        main = main_document_part(zf)
        if main is None:
            raise CorruptFileError("The DOCX file has no Word document inside.")
        self.main = main
        self.rels = read_rels(zf, main)
        self.styles = _load_styles(self._part("styles"))
        self.numbering = _load_numbering(self._part("numbering"))
        self.sections = SectionTracker()
        self.units: list[TextUnit] = []

    # ---------- package parts ----------
    def _xml(self, path: str) -> etree._Element | None:
        try:
            return parse_xml(self.zf.read(path))
        except KeyError:
            return None
        except etree.XMLSyntaxError as exc:
            raise CorruptFileError("The DOCX file contains damaged XML.", {"part": path}) from exc

    def _part(self, kind: str) -> etree._Element | None:
        for _, k, target in self.rels:
            if k == kind:
                return self._xml(target)
        return None

    def _parts(self, kind: str) -> list[etree._Element]:
        seen, out = set(), []
        for _, k, target in self.rels:
            if k == kind and target not in seen:
                seen.add(target)
                root = self._xml(target)
                if root is not None:
                    out.append(root)
        return out

    # ---------- styles ----------
    def _style_chain(self, style_id: str | None):  # noqa: ANN202
        seen = set()
        while style_id and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            yield self.styles[style_id]
            style_id = self.styles[style_id].based_on

    def _heading_level(self, p: etree._Element) -> int | None:
        ppr = child(p, "pPr")
        style_id = attr(child(ppr, "pStyle"), "val")
        style = self.styles.get(style_id or "")
        name = (style.name if style else style_id or "").strip()
        if name.lower() == "title":
            return 1
        m = re.match(r"heading\s*(\d)$", name, re.IGNORECASE)
        if m:
            return int(m.group(1)) + 1  # Title sits above Heading 1
        outline = _int(attr(child(ppr, "outlineLvl"), "val"))
        if outline is None:
            outline = next((s.outline for s in self._style_chain(style_id) if s.outline is not None), None)
        return outline + 2 if outline is not None and 0 <= outline < 9 else None

    def _list_label(self, p: etree._Element) -> str | None:
        ppr = child(p, "pPr")
        num_pr = child(ppr, "numPr")
        num_id = attr(child(num_pr, "numId"), "val")
        ilvl = _int(attr(child(num_pr, "ilvl"), "val"))
        if num_id is None or ilvl is None:
            style_id = attr(child(ppr, "pStyle"), "val")
            for st in self._style_chain(style_id):
                num_id = num_id or st.num_id
                ilvl = ilvl if ilvl is not None else st.ilvl
        if not num_id or num_id == "0":
            return None
        return self.numbering.label(num_id, min(max(ilvl or 0, 0), 8))

    # ---------- inline text ----------
    def _inline(self, el: etree._Element, out: list[str], boxes: list[etree._Element]) -> None:
        for ch in el:
            name = local(ch)
            if not name or name in _SKIP:
                continue
            if name == "AlternateContent":  # Choice (modern) and Fallback (VML) hold the same content
                pick = _alternate(ch)
                if pick is not None:
                    self._inline(pick, out, boxes)
                continue
            if name == "txbxContent":
                boxes.append(ch)
            elif name == "t":
                out.append(ch.text or "")
            elif name == "tab":
                out.append("\t")
            elif name in ("br", "cr"):
                out.append("\n")
            elif name == "noBreakHyphen":
                out.append("-")
            elif name in ("p", "tbl"):  # block content nested inline (rare): treat as a box
                boxes.append(ch)
            else:
                self._inline(ch, out, boxes)

    def _paragraph_text(self, p: etree._Element) -> tuple[str, list[etree._Element]]:
        out: list[str] = []
        boxes: list[etree._Element] = []
        self._inline(p, out, boxes)
        return "".join(out).strip(), boxes

    # ---------- tables ----------
    @staticmethod
    def _iter_named(el: etree._Element, name: str):  # noqa: ANN205
        """Children named ``name``, looking through content controls / tracked-change wrappers."""
        for ch in el:
            n = local(ch)
            if n == name:
                yield ch
            elif n in _BLOCK_WRAPPERS:
                inner = child(ch, "sdtContent") if n == "sdt" else ch
                if inner is not None:
                    yield from _Reader._iter_named(inner, name)

    def _cell_text(self, tc: etree._Element) -> str:
        parts: list[str] = []
        for block in self._iter_blocks(tc):
            if local(block) == "p":
                text, boxes = self._paragraph_text(block)
                label = self._list_label(block)
                if text:
                    parts.append(f"{label} {text}" if label else text)
                for box in boxes:
                    parts.append(self._cell_text(box))
            elif local(block) == "tbl":
                rows = self._table_rows(block)
                parts.append("; ".join(" / ".join(c for c in r if c) for r in rows if any(r)))
        return " ".join(p for p in parts if p)

    def _table_rows(self, tbl: etree._Element) -> list[list[str]]:
        rows: list[list[str]] = []
        above: dict[int, str] = {}  # column -> text of the cell above (for vertical merges)
        for tr in self._iter_named(tbl, "tr"):
            before = _int(attr(child(child(tr, "trPr"), "gridBefore"), "val"), 0) or 0
            cells: list[str] = [""] * before
            col = before
            for tc in self._iter_named(tr, "tc"):
                tcpr = child(tc, "tcPr")
                span = max(_int(attr(child(tcpr, "gridSpan"), "val"), 1) or 1, 1)
                vmerge = child(tcpr, "vMerge")
                if vmerge is not None and (attr(vmerge, "val") or "continue") == "continue":
                    text = above.get(col, "")  # continuation of a vertically merged cell
                else:
                    text = self._cell_text(tc)
                cells.append(text)
                cells.extend([""] * (span - 1))  # a horizontally merged cell fills its columns once
                for k in range(col, col + span):
                    above[k] = text if k == col else ""
                col += span
            rows.append(cells)
        return rows

    # ---------- blocks ----------
    def _iter_blocks(self, container: etree._Element):  # noqa: ANN202
        for ch in container:
            name = local(ch)
            if name in ("p", "tbl"):
                yield ch
            elif name == "sdt":
                content = child(ch, "sdtContent")
                if content is not None:
                    yield from self._iter_blocks(content)
            elif name == "AlternateContent":
                pick = _alternate(ch)
                if pick is not None:
                    yield from self._iter_blocks(pick)
            elif name in _BLOCK_WRAPPERS:
                yield from self._iter_blocks(ch)

    def walk(self, container: etree._Element, section: str | None = None, headings: bool = True) -> None:
        """Turn the blocks of ``container`` into units (``section`` overrides the heading path)."""
        for block in self._iter_blocks(container):
            if local(block) == "tbl":
                md = rows_to_markdown(self._table_rows(block))  # type: ignore[arg-type]
                if md:
                    self.units.append(TextUnit(text=md, section=section or self.sections.path, kind="table"))
                continue
            text, boxes = self._paragraph_text(block)
            if text:
                level = self._heading_level(block) if headings else None
                if level and len(text) <= 300:
                    self.sections.push(level, " ".join(text.split()))
                else:
                    label = self._list_label(block)
                    self.units.append(
                        TextUnit(text=f"{label} {text}" if label else text, section=section or self.sections.path)
                    )
            for box in boxes:  # text boxes and shapes anchored in this paragraph
                self.walk(box, section=section, headings=False)

    def notes(self, kind: str, title: str) -> None:
        """Footnotes or endnotes, each as '[n] text'."""
        for root in self._parts(kind):
            for note in root:
                if local(note) not in ("footnote", "endnote"):
                    continue
                if attr(note, "type") in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                text = self._cell_text(note)
                if text:
                    self.units.append(TextUnit(text=f"[{attr(note, 'id')}] {text}", section=title))

    def headers_footers(self) -> None:
        """Header and footer text once each (page numbers are dropped later by cleaning)."""
        seen: set[str] = set()
        for kind in ("header", "footer"):
            for root in self._parts(kind):
                for block in self._iter_blocks(root):
                    text = self._cell_text(block) if local(block) == "tbl" else self._paragraph_text(block)[0]
                    key = " ".join(text.split()).lower()
                    if text and key not in seen:
                        seen.add(key)
                        self.units.append(TextUnit(text=text, section="Page header and footer"))


def extract_docx(data: bytes) -> ExtractedDocument:
    """Extract headings, paragraphs, lists, tables, text boxes and notes from a DOCX in reading order."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise CorruptFileError("The DOCX file is corrupt or unreadable.") from exc
    with zf:
        reader = _Reader(zf)
        root = reader._xml(reader.main)
        if root is None:
            raise CorruptFileError("The DOCX file has no Word document inside.")
        body = child(root, "body")
        reader.walk(body if body is not None else root)
        reader.notes("footnotes", "Footnotes")
        reader.notes("endnotes", "Endnotes")
        reader.headers_footers()
    return ExtractedDocument(units=reader.units, page_count=None)
