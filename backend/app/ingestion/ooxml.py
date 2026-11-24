"""Small, namespace-agnostic helpers for reading Office Open XML (DOCX) packages.

Elements are matched by their *local* name, so Transitional and Strict OOXML
(different namespaces, same element names) are read the same way. XML is
parsed with entity resolution and network access switched off (no XXE).
"""

import posixpath
import zipfile

from lxml import etree

WORD_MAIN_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
)

_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True, remove_pis=True
)


def parse_xml(data: bytes) -> etree._Element:
    """Parse untrusted XML safely."""
    return etree.fromstring(data, parser=_PARSER)


def local(el: etree._Element) -> str:
    """Local name of an element ('p' for '{ns}p'); '' for comments and the like."""
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def attr(el: etree._Element | None, name: str) -> str | None:
    """Attribute value matched by local name (``w:val`` -> ``attr(el, 'val')``)."""
    if el is None:
        return None
    for key, value in el.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return None


def child(el: etree._Element | None, name: str) -> etree._Element | None:
    """First direct child with this local name."""
    if el is None:
        return None
    for ch in el:
        if local(ch) == name:
            return ch
    return None


def rels_path(part: str) -> str:
    """'word/document.xml' -> 'word/_rels/document.xml.rels'."""
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{name}.rels")


def read_rels(zf: zipfile.ZipFile, part: str) -> list[tuple[str, str, str]]:
    """Return (id, type suffix, resolved target) for the internal relationships of ``part``."""
    path = rels_path(part) if part else "_rels/.rels"
    try:
        root = parse_xml(zf.read(path))
    except (KeyError, etree.XMLSyntaxError):
        return []
    base = posixpath.dirname(part)
    out = []
    for rel in root:
        if local(rel) != "Relationship" or (attr(rel, "TargetMode") or "").lower() == "external":
            continue
        target = attr(rel, "Target") or ""
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
        out.append((attr(rel, "Id") or "", (attr(rel, "Type") or "").rsplit("/", 1)[-1], resolved))
    return out


def main_document_part(zf: zipfile.ZipFile) -> str | None:
    """Locate the main Word document part, or None when the package is not a Word document.

    The main part is normally ``word/document.xml`` but its real name is given by
    the package relationships, and some tools use another name.
    """
    names = set(zf.namelist())
    for _, kind, target in read_rels(zf, ""):
        if kind == "officeDocument" and target in names:
            if _is_word_part(zf, target):
                return target
    return "word/document.xml" if "word/document.xml" in names else None


def _is_word_part(zf: zipfile.ZipFile, part: str) -> bool:
    """Check [Content_Types].xml says ``part`` is a Word main document (not Excel/PowerPoint)."""
    try:
        root = parse_xml(zf.read("[Content_Types].xml"))
    except (KeyError, etree.XMLSyntaxError):
        return part.startswith("word/")
    for el in root:
        if local(el) == "Override" and (attr(el, "PartName") or "").lstrip("/") == part:
            return (attr(el, "ContentType") or "") in WORD_MAIN_TYPES
    return part.startswith("word/")
