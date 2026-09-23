"""Hand-built .xlsx packages for tests: zipfile + XML only (no openpyxl), full control of every part.

Everything is INVENTED test data.  The builder can produce the shapes real exporters produce
(inline / shared / formula strings, missing cell coordinates, several sheets, namespace prefixes, the
strict OOXML namespace, poisoned ``styles.xml``) and deliberately broken packages (missing or broken
relationships) so the reader's error handling can be tested.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_MAIN_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
NS_REL_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


@dataclass
class Rich:
    """A rich-text string: one ``<r><t>`` per run, plus optional phonetic hints that must not be part of the text."""

    runs: list[str]
    phonetic: str | None = None


@dataclass
class Raw:
    """A ready-made ``<c>`` element; ``{ref}`` is replaced by the ``r="A1"`` attribute (or nothing)."""

    xml: str


@dataclass
class Row:
    """A row with an explicit Excel row number (use it to create gaps)."""

    number: int
    cells: list[object] = field(default_factory=list)


def column_letters(index: int) -> str:
    letters, number = "", index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def styles_xml(fills: str = '<fills count="2"><fill><patternFill patternType="none"/></fill>'
               '<fill><patternFill patternType="gray125"/></fill></fills>') -> str:
    """A small ``styles.xml``; pass a different ``fills`` block to make it unreadable for some libraries."""
    return (
        f'{XML_DECLARATION}<styleSheet xmlns="{NS_MAIN}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        f"{fills}"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="14" fontId="0" fillId="1" borderId="0" xfId="0" applyFill="1"/></cellXfs></styleSheet>'
    )


# Fill blocks that make openpyxl's stylesheet parser fail (verified against openpyxl 3.1.5):
# the first three raise "TypeError: Fill() takes no arguments" -> "expected <class '...Fill'>".
POISON_FILLS = {
    "empty fill element": '<fills count="2"><fill/><fill/></fills>',
    "fill with only an attribute": '<fills count="1"><fill patternType="solid"/></fills>',
    "fill with stray text": '<fills count="1"><fill>text</fill></fills>',
    "colour directly inside fill": '<fills count="1"><fill><fgColor rgb="FFFF0000"/></fill></fills>',
}


def _rich_runs(value: Rich, tag) -> str:
    runs = "".join(f'<{tag("r")}><{tag("t")} xml:space="preserve">{escape(run)}</{tag("t")}></{tag("r")}>' for run in value.runs)
    if value.phonetic is not None:
        runs += f'<{tag("rPh")} sb="0" eb="1"><{tag("t")}>{escape(value.phonetic)}</{tag("t")}></{tag("rPh")}>'
    return runs


def _string_item(value: object, tag) -> str:
    if isinstance(value, Rich):
        return _rich_runs(value, tag)
    return f'<{tag("t")} xml:space="preserve">{escape(str(value))}</{tag("t")}>'


def write_xlsx(
    path: Path,
    sheets: list[tuple[str, list]],
    *,
    string_style: str = "inline",        # inline | shared | formula
    coordinates: bool = True,            # write r="A1" on cells (and r on rows when row_numbers)
    row_numbers: bool = True,
    keep_empty: bool = False,            # write an empty <c/> for None cells (keeps positions without coordinates)
    styles: str | None = None,           # None: no styles part; otherwise the styles.xml text
    style_id_on_cells: bool = False,     # add s="1" to cells so they point at the (possibly poisoned) styles
    date1904: bool = False,
    prefix: str = "",                    # namespace prefix for every element, e.g. "x" -> <x:row>
    strict: bool = False,                # ISO strict OOXML namespaces
    break_package: str | None = None,    # see below
    extra_parts: dict[str, str | bytes] | None = None,
) -> Path:
    """Write ``sheets`` = ``[(sheet_name, rows)]``; a row is a list of cell values or a ``Row``.

    Cell values: ``None`` (empty), ``str``, ``int``/``float`` (numeric), ``bool``, ``Rich`` or ``Raw``.

    ``break_package`` damages the package: ``missing_workbook``, ``missing_rels``, ``unknown_rid``,
    ``missing_rid``, ``missing_target``, ``wrong_type``, ``bad_workbook_xml``, ``bad_rels_xml``, ``no_sheets``.
    """
    ns_main, ns_rel = (NS_MAIN_STRICT, NS_REL_STRICT) if strict else (NS_MAIN, NS_REL)

    def tag(name: str) -> str:
        return f"{prefix}:{name}" if prefix else name

    xmlns = f'xmlns:{prefix}="{ns_main}"' if prefix else f'xmlns="{ns_main}"'
    shared: list[object] = []

    def cell_xml(row_no: int, col: int, value: object) -> str:
        ref_attr = f' r="{column_letters(col)}{row_no}"' if coordinates else ""
        style_attr = ' s="1"' if style_id_on_cells else ""
        c = tag("c")
        if value is None:
            return f"<{c}{ref_attr}{style_attr}/>" if keep_empty else ""
        if isinstance(value, Raw):
            return value.xml.replace("{ref}", ref_attr)
        if isinstance(value, bool):
            return f'<{c}{ref_attr}{style_attr} t="b"><{tag("v")}>{int(value)}</{tag("v")}></{c}>'
        if isinstance(value, (int, float)):
            return f'<{c}{ref_attr}{style_attr}><{tag("v")}>{value!r}</{tag("v")}></{c}>'
        if string_style == "shared":
            shared.append(value)
            return f'<{c}{ref_attr}{style_attr} t="s"><{tag("v")}>{len(shared) - 1}</{tag("v")}></{c}>'
        if string_style == "formula" and not isinstance(value, Rich):
            return f'<{c}{ref_attr}{style_attr} t="str"><{tag("f")}>"x"</{tag("f")}><{tag("v")}>{escape(str(value))}</{tag("v")}></{c}>'
        return f'<{c}{ref_attr}{style_attr} t="inlineStr"><{tag("is")}>{_string_item(value, tag)}</{tag("is")}></{c}>'

    def sheet_xml(rows: list) -> str:
        body, row_no = [], 0
        for entry in rows:
            if isinstance(entry, Row):
                row_no, cells = entry.number, entry.cells
            else:
                row_no, cells = row_no + 1, entry
            row_attr = f' r="{row_no}"' if row_numbers else ""
            cells_xml = "".join(cell_xml(row_no, col, value) for col, value in enumerate(cells))
            body.append(f'<{tag("row")}{row_attr}>{cells_xml}</{tag("row")}>')
        return (f'{XML_DECLARATION}<{tag("worksheet")} {xmlns}><{tag("sheetData")}>{"".join(body)}'
                f'</{tag("sheetData")}></{tag("worksheet")}>')

    sheet_parts = {f"xl/worksheets/sheet{i}.xml": sheet_xml(rows) for i, (_, rows) in enumerate(sheets, start=1)}

    parts: dict[str, str | bytes] = {}
    overrides = [
        ("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
        *[(f"/{name}", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml") for name in sheet_parts],
    ]
    rel_items = []
    for i, (name, _) in enumerate(sheets, start=1):
        target, rel_type = f"worksheets/sheet{i}.xml", f"{ns_rel}/worksheet"
        if break_package == "missing_target" and i == 1:
            target = "worksheets/does-not-exist.xml"
        if break_package == "wrong_type" and i == 1:
            rel_type = f"{ns_rel}/chartsheet"
        rel_items.append(f'<Relationship Id="rId{i}" Type="{rel_type}" Target="{target}"/>')
    parts.update(sheet_parts)
    if shared:
        items = "".join(f'<{tag("si")}>{_string_item(v, tag)}</{tag("si")}>' for v in shared)
        parts["xl/sharedStrings.xml"] = (
            f'{XML_DECLARATION}<{tag("sst")} {xmlns} count="{len(shared)}" uniqueCount="{len(shared)}">{items}</{tag("sst")}>'
        )
        overrides.append(("/xl/sharedStrings.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"))
        rel_items.append(f'<Relationship Id="rId900" Type="{ns_rel}/sharedStrings" Target="sharedStrings.xml"/>')
    if styles is not None:
        parts["xl/styles.xml"] = styles
        overrides.append(("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"))
        rel_items.append(f'<Relationship Id="rId901" Type="{ns_rel}/styles" Target="styles.xml"/>')

    sheet_elements = []
    for i, (name, _) in enumerate(sheets, start=1):
        rid = f' r:id="rId{i}"'
        if break_package == "missing_rid" and i == 1:
            rid = ""
        if break_package == "unknown_rid" and i == 1:
            rid = ' r:id="rId99"'
        sheet_elements.append(f'<{tag("sheet")} name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}"{rid}/>')
    sheets_xml = "" if break_package == "no_sheets" else "".join(sheet_elements)
    workbook_pr = f'<{tag("workbookPr")} date1904="1"/>' if date1904 else ""
    parts["xl/workbook.xml"] = (
        f'{XML_DECLARATION}<{tag("workbook")} {xmlns} xmlns:r="{ns_rel}">{workbook_pr}'
        f'<{tag("sheets")}>{sheets_xml}</{tag("sheets")}></{tag("workbook")}>'
    )
    parts["xl/_rels/workbook.xml.rels"] = f'{XML_DECLARATION}<Relationships xmlns="{NS_PKG_REL}">{"".join(rel_items)}</Relationships>'
    parts["_rels/.rels"] = (
        f'{XML_DECLARATION}<Relationships xmlns="{NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{ns_rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    parts["[Content_Types].xml"] = (
        f'{XML_DECLARATION}<Types xmlns="{NS_CONTENT_TYPES}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in overrides)
        + "</Types>"
    )

    if break_package == "missing_workbook":
        del parts["xl/workbook.xml"]
    if break_package == "missing_rels":
        del parts["xl/_rels/workbook.xml.rels"]
    if break_package == "bad_workbook_xml":
        parts["xl/workbook.xml"] = "<workbook><sheets><sheet"
    if break_package == "bad_rels_xml":
        parts["xl/_rels/workbook.xml.rels"] = "<Relationships><Relationship"
    parts.update(extra_parts or {})

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in parts.items():
            package.writestr(name, data)
    return Path(path)
