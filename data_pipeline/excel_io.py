"""Streaming, read-only access to the raw workbooks - standard library only.

An ``.xlsx`` file is a ZIP package of XML parts.  This module reads those parts directly
(``zipfile`` + ``xml.etree.ElementTree``) and deliberately ignores everything about
presentation: ``xl/styles.xml`` is never opened, so workbooks whose styles other libraries
cannot parse (for example an empty ``<fill/>`` makes ``openpyxl`` fail with
``TypeError: Fill() takes no arguments``) are read like any other.

* Nothing here ever writes to a workbook.
* Worksheet XML is streamed with ``iterparse``; only the current ``<row>`` is held in memory
  and processed elements are released immediately.
* The shared-string table (if the package has one) is the only structure proportional to the
  size of the workbook.
* Number formats are not interpreted: numeric cells are returned as ``int`` / ``float`` and
  the parsers in ``parsing.py`` convert Excel serial dates when a date column needs it.
"""

from __future__ import annotations

import hashlib
import itertools
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from data_pipeline.schemas import SourceSpec, normalize_header

HEADER_SCAN_ROWS = 25


class SchemaError(RuntimeError):
    """The workbook is damaged or does not contain the columns the pipeline expects."""


@dataclass(slots=True)
class SheetLayout:
    sheet_name: str
    header_row: int                 # 1-based Excel row of the header
    headers: list[str]              # header cells exactly as found
    positions: dict[str, int]       # canonical column -> 0-based position
    header_for: dict[str, str]      # canonical column -> header text as found
    unmapped_headers: list[str]     # headers present in the file but not declared in the spec
    duplicate_headers: list[str]    # repeated headers (first one wins)
    title: str | None               # e.g. "Works Recommended" (row above the header)
    other_sheets: list[str] = field(default_factory=list)   # further sheets in the file - NOT read
    date1904: bool = False          # workbook uses the 1904 date system (numeric dates would be off)


def file_fingerprint(path: Path) -> dict[str, object]:
    """Size, modification time and SHA-256 - identifies the exact snapshot processed."""
    stat = path.stat()
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
    }


def _alias_map(spec: SourceSpec) -> dict[str, str]:
    return {normalize_header(alias): col.name for col in spec.columns for alias in col.aliases}


def detect_layout(spec: SourceSpec, rows: Sequence[Sequence[object]], sheet_name: str) -> SheetLayout | None:
    """Find the header row among the first rows (title rows above it are skipped)."""
    aliases = _alias_map(spec)
    required = {c.name for c in spec.columns if c.required}
    for index, row in enumerate(rows):
        positions: dict[str, int] = {}
        header_for: dict[str, str] = {}
        unmapped: list[str] = []
        duplicates: list[str] = []
        for pos, cell in enumerate(row):
            if cell is None or not str(cell).strip():
                continue
            canonical = aliases.get(normalize_header(cell))
            if canonical is None:
                unmapped.append(str(cell).strip())
            elif canonical in positions:
                duplicates.append(str(cell).strip())
            else:
                positions[canonical] = pos
                header_for[canonical] = str(cell).strip()
        if required <= positions.keys():
            title = next(
                (str(c).strip() for earlier in rows[:index] for c in earlier if c is not None and str(c).strip()),
                None,
            )
            headers = [str(c).strip() if c is not None else "" for c in row]
            return SheetLayout(sheet_name, index + 1, headers, positions, header_for, unmapped, duplicates, title)
    return None


def _diagnose(spec: SourceSpec, rows: Sequence[Sequence[object]]) -> str:
    aliases = _alias_map(spec)
    required = {c.name for c in spec.columns if c.required}
    best_missing, best_index = sorted(required), None
    for index, row in enumerate(rows):
        found = {aliases.get(normalize_header(c)) for c in row if c is not None and str(c).strip()}
        missing = sorted(required - found)
        if len(missing) < len(best_missing):
            best_missing, best_index = missing, index
    preview = [[str(c)[:30] for c in row if c is not None][:12] for row in rows[:4]]
    where = f"closest header row: {best_index + 1}" if best_index is not None else "no header-like row found"
    return f"{where}; missing required columns: {best_missing}; first rows: {preview}"


# ------------------------------------------------------------------------- package parts
_XHHHH_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")
_STREAM_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decode_escapes(text: str) -> str:
    """Excel writes characters XML cannot hold as ``_xHHHH_`` (``_x005F_`` is a literal underscore)."""

    def replace(match: re.Match[str]) -> str:
        code = int(match.group(1), 16)
        return "\ufffd" if 0xD800 <= code <= 0xDFFF else chr(code)

    return _XHHHH_RE.sub(replace, text)


def _string_item(item: ET.Element, t_tag: str, r_tag: str) -> str:
    """Text of a shared-string ``<si>`` or an inline ``<is>``: a plain ``<t>`` or the ``<t>`` of every
    rich-text run ``<r>``.  Phonetic hints (``<rPh>``) are not part of the text and are skipped."""
    if len(item) == 1 and item[0].tag == t_tag:  # the common case
        text = item[0].text or ""
    else:
        parts: list[str] = []
        for child in item:
            if child.tag == t_tag:
                parts.append(child.text or "")
            elif child.tag == r_tag:
                parts.extend(grand.text or "" for grand in child if grand.tag == t_tag)
        text = "".join(parts)
    return _decode_escapes(text) if "_x" in text else text


def _col_index(ref: str) -> int:
    """0-based column of an A1-style reference (``"C7"`` -> 2); -1 if the reference has no letters."""
    index = 0
    for char in ref:
        code = ord(char)
        if 65 <= code <= 90:
            index = index * 26 + code - 64
        elif 97 <= code <= 122:
            index = index * 26 + code - 96
        else:
            break
    return index - 1


def _number(text: str) -> object:
    """Numeric cell text -> ``int`` / ``float`` (as Excel readers do); odd text is returned unchanged."""
    try:
        if "." in text or "E" in text or "e" in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _resolve_member(names: set[str], base_dir: str, target: str) -> str | None:
    """Zip member a relationship ``Target`` points at (relative to ``base_dir`` unless it starts with ``/``)."""
    target = target.replace("\\", "/")
    if target.startswith("/"):
        candidates = [posixpath.normpath(target.lstrip("/"))]
    else:
        # some writers give the path relative to the package root instead
        candidates = [posixpath.normpath(posixpath.join(base_dir, target)), posixpath.normpath(target)]
    return next((c for c in candidates if c in names), None)


def _read_xml_part(archive: zipfile.ZipFile, part: str, workbook: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(part))
    except KeyError as error:
        raise SchemaError(f"{workbook}: package part '{part}' is missing") from error
    except ET.ParseError as error:
        raise SchemaError(f"{workbook}: package part '{part}' is not valid XML ({error})") from error
    except _STREAM_ERRORS as error:
        raise SchemaError(f"{workbook}: package part '{part}' could not be read from the ZIP ({error})") from error


@dataclass(slots=True)
class _SheetRef:
    name: str
    member: str | None      # zip member holding the worksheet XML; None when it cannot be resolved
    problem: str = ""       # why it could not be resolved


@dataclass(slots=True)
class _Package:
    sheets: list[_SheetRef]
    shared_strings: str | None
    date1904: bool


def _read_package(archive: zipfile.ZipFile, workbook: str) -> _Package:
    """Workbook part -> sheet names -> worksheet parts, resolved through the relationship parts."""
    names = set(archive.namelist())
    workbook_part = "xl/workbook.xml"
    if "_rels/.rels" in names:
        try:
            root_rels: ET.Element | None = ET.fromstring(archive.read("_rels/.rels"))
        except (ET.ParseError, *_STREAM_ERRORS):
            root_rels = None  # fall back to the conventional location
        for rel in root_rels.iter() if root_rels is not None else ():
            if _local(rel.tag) == "Relationship" and rel.get("Type", "").endswith("/officeDocument"):
                workbook_part = _resolve_member(names, "", rel.get("Target", "")) or workbook_part
                break
    if workbook_part not in names:
        raise SchemaError(f"{workbook}: workbook part 'xl/workbook.xml' is missing - not an Excel .xlsx package?")

    book = _read_xml_part(archive, workbook_part, workbook)
    listed: list[tuple[str, str | None]] = []  # (sheet name, relationship id)
    date1904 = False
    for element in book.iter():
        local = _local(element.tag)
        if local == "sheet":
            rel_id = next((v for k, v in element.attrib.items() if k.startswith("{") and k.endswith("}id")), None)
            listed.append((element.get("name") or "", rel_id))
        elif local == "workbookPr":
            date1904 = (element.get("date1904") or "").lower() in ("1", "true")
    if not listed:
        raise SchemaError(f"{workbook}: '{workbook_part}' lists no sheets")

    base_dir = posixpath.dirname(workbook_part)
    rels_part = posixpath.join(base_dir, "_rels", posixpath.basename(workbook_part) + ".rels")
    if rels_part not in names:
        raise SchemaError(
            f"{workbook}: workbook relationships '{rels_part}' are missing, so no worksheet can be resolved"
        )
    rels: dict[str, tuple[str, str]] = {}
    for rel in _read_xml_part(archive, rels_part, workbook).iter():
        if _local(rel.tag) == "Relationship" and rel.get("TargetMode") != "External":
            rels[rel.get("Id", "")] = (rel.get("Type", ""), rel.get("Target", ""))

    sheets = []
    for name, rel_id in listed:
        rel = rels.get(rel_id or "")
        if rel is None:
            sheets.append(_SheetRef(name, None, f"relationship id {rel_id!r} not found in '{rels_part}'"))
        elif not rel[0].endswith("/worksheet"):
            sheets.append(_SheetRef(name, None, f"not a worksheet (relationship type '{rel[0].rsplit('/', 1)[-1]}')"))
        else:
            member = _resolve_member(names, base_dir, rel[1])
            sheets.append(_SheetRef(name, member, "" if member else f"relationship target '{rel[1]}' is not in the package"))

    shared = next(
        (_resolve_member(names, base_dir, target) for kind, target in rels.values() if kind.endswith("/sharedStrings")),
        None,
    ) or ("xl/sharedStrings.xml" if "xl/sharedStrings.xml" in names else None)
    return _Package(sheets, shared, date1904)


def _load_shared_strings(archive: zipfile.ZipFile, member: str, workbook: str) -> list[str]:
    """The shared-string table (only read when the package has one)."""
    strings: list[str] = []
    try:
        with archive.open(member) as stream:
            root: ET.Element | None = None
            si_tag = t_tag = r_tag = ""
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    if root is None:  # <sst>: learn its namespace (main or strict) from the tag
                        root = element
                        namespace = element.tag[: element.tag.index("}") + 1] if element.tag.startswith("{") else ""
                        si_tag, t_tag, r_tag = namespace + "si", namespace + "t", namespace + "r"
                elif element.tag == si_tag:
                    strings.append(_string_item(element, t_tag, r_tag))
                    root.clear()  # type: ignore[union-attr]  # release processed items: memory stays flat
    except ET.ParseError as error:
        raise SchemaError(f"{workbook}: shared strings '{member}' are not valid XML ({error})") from error
    except _STREAM_ERRORS as error:
        raise SchemaError(f"{workbook}: shared strings '{member}' could not be read from the ZIP ({error})") from error
    return strings


def _iter_sheet_rows(
    archive: zipfile.ZipFile, member: str, shared: list[str] | None, workbook: str, sheet: str
) -> Iterator[tuple[int, list[object]]]:
    """Stream ``(excel_row_number, cell_values)`` for every ``<row>`` of a worksheet, in file order.

    ``cell_values`` is indexed by 0-based column; trailing empty cells are omitted.  Cells are
    placed by their ``r`` reference when present (so gaps and out-of-order cells are handled) and
    sequentially otherwise.  Formulas yield their cached ``<v>`` value.  Styles are never consulted.
    """
    where = f"{workbook}: worksheet '{sheet}' ({member})"
    try:
        with archive.open(member) as stream:
            root: ET.Element | None = None
            sheet_data: ET.Element | None = None
            row_tag = c_tag = v_tag = is_tag = t_tag = r_tag = sheet_data_tag = ""
            values: list[object] = []
            next_col = 0
            last_row = 0
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    if root is None:  # <worksheet>: namespace (main or strict) comes from its tag
                        root = element
                        ns = element.tag[: element.tag.index("}") + 1] if element.tag.startswith("{") else ""
                        row_tag, c_tag, v_tag, is_tag, t_tag, r_tag, sheet_data_tag = (
                            ns + "row", ns + "c", ns + "v", ns + "is", ns + "t", ns + "r", ns + "sheetData",
                        )
                    elif element.tag == sheet_data_tag:
                        sheet_data = element
                    continue

                tag = element.tag
                if tag == c_tag:
                    reference = element.get("r")
                    column = _col_index(reference) if reference else -1
                    if column < 0:
                        column = next_col
                    next_col = column + 1
                    kind = element.get("t")
                    if kind == "inlineStr":
                        inline = element.find(is_tag)
                        value: object = _string_item(inline, t_tag, r_tag) if inline is not None else None
                    else:
                        node = element.find(v_tag)
                        text = node.text if node is not None else None
                        if text is None or text == "":
                            value = None
                        elif kind is None or kind == "n":
                            value = _number(text)
                        elif kind == "s":
                            if shared is None:
                                raise SchemaError(f"{where}: a cell uses shared strings but the package has no sharedStrings part")
                            try:
                                value = shared[int(text)]
                            except (ValueError, IndexError):
                                raise SchemaError(
                                    f"{where}: shared string index {text!r} is invalid (table has {len(shared)} entries)"
                                ) from None
                        elif kind == "b":
                            value = text.strip().lower() not in ("0", "false")
                        else:  # "str" (formula text), "e" (error such as #N/A), "d" (ISO date), anything else
                            value = _decode_escapes(text) if "_x" in text else text
                    if value is not None and value != "":
                        if column >= len(values):
                            values.extend([None] * (column + 1 - len(values)))
                        values[column] = value
                    element.clear()
                elif tag == row_tag:
                    number = element.get("r")
                    row_no = int(number) if number and number.isdigit() else last_row + 1
                    yield row_no, values
                    values, next_col, last_row = [], 0, row_no
                    if sheet_data is not None:
                        sheet_data.clear()  # release processed rows: memory stays flat
                    else:
                        element.clear()
    except ET.ParseError as error:
        raise SchemaError(f"{where} is not valid XML ({error})") from error
    except _STREAM_ERRORS as error:
        raise SchemaError(f"{where} could not be read from the ZIP ({error})") from error


def _fill_gaps(rows: Iterator[tuple[int, list[object]]], first: int) -> Iterator[tuple[int, list[object]]]:
    """Rows without a ``<row>`` element are blank rows: yield them so row numbers stay aligned."""
    expected = first
    for row_no, values in rows:
        while expected < row_no:
            yield expected, []
            expected += 1
        yield row_no, values
        expected = max(expected, row_no + 1)


class SourceReader:
    """Context manager yielding ``(excel_row_number, {canonical_column: raw_value})``."""

    def __init__(self, spec: SourceSpec, path: Path) -> None:
        self.spec = spec
        self.path = Path(path)
        self.layout: SheetLayout | None = None
        self._archive: zipfile.ZipFile | None = None
        self._raw_rows: Iterator[tuple[int, list[object]]] | None = None
        self._numbered: Iterator[tuple[int, list[object]]] | None = None

    def __enter__(self) -> "SourceReader":
        name = self.path.name
        try:
            self._archive = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as error:
            raise SchemaError(
                f"{name}: not a valid .xlsx (ZIP) package ({error}); is it an old .xls, encrypted or truncated file?"
            ) from error
        try:
            self._open_first_matching_sheet(name)
        except BaseException:
            self._close()
            raise
        return self

    def _open_first_matching_sheet(self, name: str) -> None:
        assert self._archive is not None
        package = _read_package(self._archive, name)
        readable = [sheet for sheet in package.sheets if sheet.member]
        problems = [f"sheet {s.name!r}: {s.problem}" for s in package.sheets if not s.member]
        if not readable:
            raise SchemaError(f"{name}: no worksheet could be resolved - " + " | ".join(problems))
        shared = _load_shared_strings(self._archive, package.shared_strings, name) if package.shared_strings else None

        for sheet in readable:
            assert sheet.member is not None
            stream = _iter_sheet_rows(self._archive, sheet.member, shared, name, sheet.name)
            found: list[tuple[int, list[object]]] = []
            pending: list[tuple[int, list[object]]] = []
            for row_no, values in stream:  # only the first HEADER_SCAN_ROWS rows are needed to find the header
                if row_no > HEADER_SCAN_ROWS:
                    pending.append((row_no, values))
                    break
                found.append((row_no, values))
            by_number = dict(found)
            head = [by_number.get(n, []) for n in range(1, max(by_number, default=0) + 1)]  # aligned to Excel rows
            layout = detect_layout(self.spec, head, sheet.name)
            if layout is None:
                stream.close()
                problems.append(f"sheet {sheet.name!r}: {_diagnose(self.spec, head)}")
                continue
            layout.other_sheets = [s.name for s in package.sheets if s.name != sheet.name]
            layout.date1904 = package.date1904
            self.layout = layout
            self._raw_rows = stream
            after_header = itertools.chain((r for r in found if r[0] > layout.header_row), pending, stream)
            self._numbered = _fill_gaps(after_header, layout.header_row + 1)
            return
        raise SchemaError(f"{name}: expected columns not found. " + " | ".join(problems))

    def _close(self) -> None:
        for stream in (self._numbered, self._raw_rows):
            close = getattr(stream, "close", None)
            if close:
                close()
        self._numbered = self._raw_rows = None
        if self._archive is not None:
            self._archive.close()  # releases the file handle (matters on Windows)
            self._archive = None

    def __exit__(self, *exc_info: object) -> None:
        self._close()

    def rows(self) -> Iterator[tuple[int, dict[str, object]]]:
        assert self.layout is not None and self._numbered is not None, "use SourceReader as a context manager"
        pairs = tuple(self.layout.positions.items())
        for row_no, values in self._numbered:
            width = len(values)  # rows can be shorter than the header
            yield row_no, {name: (values[pos] if pos < width else None) for name, pos in pairs}
