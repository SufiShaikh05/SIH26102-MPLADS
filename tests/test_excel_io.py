import re
import zipfile

import pytest
from helpers import HEADERS, TITLES, write_workbook

from data_pipeline.excel_io import SchemaError, SourceReader, detect_layout, file_fingerprint
from data_pipeline.ingest import is_blank
from data_pipeline.schemas import RECOMMENDED, SOURCES, normalize_header


def read_all(spec, path):
    with SourceReader(spec, path) as reader:
        return reader.layout, list(reader.rows())


def test_header_variants_normalise_to_the_same_key():
    assert normalize_header("RECOMMENDED AMOUNT   ( \u20b9 )") == normalize_header("Recommended Amount (\u20b9)")
    assert normalize_header("Sanction Amount (Rs.)") == normalize_header("Sanction Amount ( \u20b9 )") == "sanction amount"
    assert normalize_header("Sanction Amount \u20b9") == "sanction amount"
    assert normalize_header("Work ID") != normalize_header("Work")   # parentheses are the only thing dropped
    assert normalize_header("Hon\u2019ble Members of Parliament") == normalize_header("Hon'ble Members of Parliament")


def test_finds_header_below_a_title_row_and_reports_row_numbers(tmp_path):
    rows = [["1", "Normal/Others", "WS/MP1/2024-2025/1-x", "Bihar", "IDA", "MP", "C", "d", "01-Jan-2024", "10", "02-Jan-2024"]]
    path = write_workbook(tmp_path / "r.xlsx", TITLES["recommended"], HEADERS["recommended"], rows)
    layout, data = read_all(RECOMMENDED, path)
    assert layout.header_row == 2 and layout.title == "Works Recommended"
    assert layout.header_for["recommended_amount"] == "RECOMMENDED AMOUNT   ( \u20b9 )"
    assert [n for n, _ in data] == [3]
    assert data[0][1]["work"] == "WS/MP1/2024-2025/1-x"
    assert data[0][1]["recommended_amount"] == "10"


def test_blank_rows_keep_excel_row_numbers_aligned(tmp_path):
    rows = [["1", "c", "WS/MP1/2024-2025/1-x", "s", "i", "m", "k", "d", "01-Jan-2024", "1", None], [], [],
            ["2", "c", "WS/MP1/2024-2025/2-x", "s", "i", "m", "k", "d", "01-Jan-2024", "1", None]]
    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", HEADERS["recommended"], rows)
    _, data = read_all(RECOMMENDED, path)
    assert [n for n, rec in data if not is_blank(rec)] == [3, 6]


def test_a_missing_required_column_stops_with_a_helpful_error(tmp_path):
    headers = [h for h in HEADERS["recommended"] if h != "State"]
    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", headers, [])
    with pytest.raises(SchemaError, match="missing required columns"):
        read_all(RECOMMENDED, path)


def test_extra_and_duplicate_headers_are_reported_not_fatal(tmp_path):
    headers = [*HEADERS["recommended"], "Remarks", "State"]
    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", headers, [])
    layout, _ = read_all(RECOMMENDED, path)
    assert layout.unmapped_headers == ["Remarks"]
    assert layout.duplicate_headers == ["State"]


def test_header_detection_works_on_a_plain_row_list():
    rows = [["Title only"], [None, None], HEADERS["allocation"], ["1", "Bihar", "X", "Y", "5"]]
    layout = detect_layout(SOURCES["allocation"], rows, "Sheet1")
    assert layout.header_row == 3 and layout.positions["allocated_amount"] == 4


def _rewrite_sheet_xml(source, target, transform):
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = transform(data.decode("utf-8")).encode("utf-8")
            zout.writestr(item, data)


@pytest.mark.parametrize("dimension", ['<dimension ref="A1"/>', ""], ids=["wrong dimension", "no dimension"])
def test_wrong_or_missing_sheet_dimension_does_not_truncate_columns(tmp_path, dimension):
    """The spike found workbooks without a <dimension>; a wrong one made openpyxl truncate. This reader ignores it."""
    rows = [["1", "c", "WS/MP1/2024-2025/1-x", "s", "i", "m", "k", "d", "01-Jan-2024", "1", "02-Jan-2024"]]
    original = write_workbook(tmp_path / "orig.xlsx", "Works Recommended", HEADERS["recommended"], rows)
    patched = tmp_path / "patched.xlsx"
    _rewrite_sheet_xml(original, patched, lambda xml: re.sub(r"<dimension[^>]*/>", dimension, xml))
    layout, data = read_all(RECOMMENDED, patched)
    assert len(layout.positions) == len(RECOMMENDED.columns)
    assert data[0][1]["sanction_date"] == "02-Jan-2024"


def test_short_rows_are_padded_with_none(tmp_path):
    rows = [["1", "c", "WS/MP1/2024-2025/1-x", "s"]]  # trailing cells missing
    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", HEADERS["recommended"], rows)
    _, data = read_all(RECOMMENDED, path)
    assert data[0][1]["state"] == "s" and data[0][1]["recommended_amount"] is None


def test_reader_never_modifies_the_workbook(tmp_path):
    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", HEADERS["recommended"], [])
    before = file_fingerprint(path)
    read_all(RECOMMENDED, path)
    assert file_fingerprint(path)["sha256"] == before["sha256"]


def test_additional_sheets_are_reported_because_they_are_not_read(tmp_path):
    from openpyxl import load_workbook

    path = write_workbook(tmp_path / "r.xlsx", "Works Recommended", HEADERS["recommended"], [])
    workbook = load_workbook(path)
    workbook.create_sheet("Sheet2").append(["more data the pipeline would otherwise ignore silently"])
    workbook.save(path)
    layout, _ = read_all(RECOMMENDED, path)
    assert layout.sheet_name == "Sheet1" and layout.other_sheets == ["Sheet2"]
