"""Workbooks written by streaming exporters are often bare-bones: inline strings, no styles part,
no <dimension>, sometimes no cell coordinates.  The reader must cope with all of them.

The packages below are hand-built (zipfile + XML), not written by openpyxl.  All values are invented.
"""

import zipfile
from xml.sax.saxutils import escape

import pytest
from helpers import HEADERS

from data_pipeline.excel_io import SourceReader
from data_pipeline.schemas import RECOMMENDED

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "{extra}</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
)
WORKBOOK = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{NS}" xmlns:r="{REL}">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>{{extra}}</Relationships>'
)
STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{NS}">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>'
)

ROWS = [
    ["Works Recommended"],
    HEADERS["recommended"],
    ["1", "Normal/Others", "WS/ MP620/2024-2025/133166-Construction of buildings", "Karnataka", "IDA", "Some MP",
     "DHARWAD", "Community Bhavan", "08-Jul-2024", "497185", "09-Jul-2024"],
    ["2", "Trust and Society", "WS/ MP620/2025-2026/133167-Construction of rooms", "Karnataka", "IDA", "Some MP",
     "DHARWAD", "College room", "08-Jul-2024", "500000", None],   # last cell empty
]


def column_letter(index):
    letters, number = "", index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_minimal_xlsx(path, rows, *, cell_type, coordinates, styles):
    shared = []

    def cell(row_no, col_no, value):
        if value is None:
            return ""
        ref = f' r="{column_letter(col_no)}{row_no}"' if coordinates else ""
        text = escape(str(value))
        if cell_type == "inline":
            return f'<c{ref} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
        if cell_type == "formula_string":
            return f'<c{ref} t="str"><v>{text}</v></c>'
        shared.append(text)  # shared strings
        return f'<c{ref} t="s"><v>{len(shared) - 1}</v></c>'

    body = "".join(
        f"<row{f' r={chr(34)}{n}{chr(34)}' if coordinates else ''}>"
        + "".join(cell(n, c, v) for c, v in enumerate(row))
        + "</row>"
        for n, row in enumerate(rows, start=1)
    )
    sheet = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>'
    extra_types, extra_rels, parts = "", "", {}
    if shared:
        parts["xl/sharedStrings.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="{NS}" count="{len(shared)}" uniqueCount="{len(shared)}">'
            + "".join(f'<si><t xml:space="preserve">{s}</t></si>' for s in shared) + "</sst>"
        )
        extra_types += '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        extra_rels += f'<Relationship Id="rId9" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>'
    if styles:
        parts["xl/styles.xml"] = STYLES
        extra_types += '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        extra_rels += f'<Relationship Id="rId8" Type="{REL}/styles" Target="styles.xml"/>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES.format(extra=extra_types))
        package.writestr("_rels/.rels", ROOT_RELS)
        package.writestr("xl/workbook.xml", WORKBOOK)
        package.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS.format(extra=extra_rels))
        package.writestr("xl/worksheets/sheet1.xml", sheet)
        for name, data in parts.items():
            package.writestr(name, data)
    return path


@pytest.mark.parametrize(
    "cell_type, coordinates, styles",
    [
        ("inline", False, False),          # the barest exporter: inline strings, no styles part, no coordinates
        ("inline", True, False),
        ("inline", True, True),
        ("shared", True, False),
        ("shared", False, True),
        ("formula_string", True, False),
    ],
)
def test_bare_bones_packages_are_read_completely(tmp_path, cell_type, coordinates, styles):
    path = write_minimal_xlsx(tmp_path / "bare.xlsx", ROWS, cell_type=cell_type, coordinates=coordinates, styles=styles)
    with SourceReader(RECOMMENDED, path) as reader:
        layout, data = reader.layout, list(reader.rows())
    assert layout.header_row == 2 and len(layout.positions) == len(RECOMMENDED.columns)
    assert [row_no for row_no, _ in data] == [3, 4]
    first, second = data[0][1], data[1][1]
    assert first["work"] == "WS/ MP620/2024-2025/133166-Construction of buildings"
    assert first["recommended_amount"] == "497185" and first["sanction_date"] == "09-Jul-2024"
    assert second["state"] == "Karnataka" and second["sanction_date"] is None
