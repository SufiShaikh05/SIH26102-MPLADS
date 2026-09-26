"""Tests for the standard-library XLSX reader.

openpyxl appears here only to (a) demonstrate the original failure and (b) write ordinary fixtures via
``helpers``; the production code never imports it.  All data is invented.
"""

import ast
import contextlib
import datetime as dt
import itertools
import os
import subprocess
import sys
import tracemalloc
import warnings
import zipfile
from pathlib import Path

import pytest
from helpers import HEADERS, poison_styles
from xlsx_builder import POISON_FILLS, Raw, Rich, Row, styles_xml, write_xlsx

from data_pipeline.build_dataset import PipelineConfig, run_pipeline
from data_pipeline.excel_io import SchemaError, SourceReader
from data_pipeline.ingest import is_blank
from data_pipeline.schemas import RECOMMENDED

AS_OF = dt.date(2026, 9, 20)
TITLE = ["Works Recommended"]
HEADER = HEADERS["recommended"]
DATA_1 = ["1", "Normal/Others", "WS/ MP620/2024-2025/133166-Construction of buildings", "Karnataka", "IDA", "Some MP",
          "DHARWAD", "Community Bhavan", "08-Jul-2024", "497185", "09-Jul-2024"]
DATA_2 = ["2", "Trust and Society", "WS/ MP620/2025-2026/133167-Construction of rooms", "Karnataka", "IDA", "Some MP",
          "DHARWAD", "College room", "08-Jul-2024", "500000", None]        # last cell empty
SHEET = [TITLE, HEADER, DATA_1, DATA_2]
STATE, IDA, AMOUNT, SANCTION = 3, 4, 9, 10                                   # column positions in the header


def read(path, spec=RECOMMENDED):
    with SourceReader(spec, path) as reader:
        return reader.layout, list(reader.rows())


def with_cells(**cells):
    """DATA_1 with some cells replaced (by column position name above)."""
    row = list(DATA_1)
    for name, value in cells.items():
        row[{"state": STATE, "ida": IDA, "amount": AMOUNT, "sanction": SANCTION}[name]] = value
    return row


def assert_standard_content(layout, data):
    assert layout.sheet_name == "Sheet1" and layout.header_row == 2 and layout.title == "Works Recommended"
    assert len(layout.positions) == len(RECOMMENDED.columns)
    assert [row_no for row_no, _ in data] == [3, 4]
    first, second = data[0][1], data[1][1]
    assert first["work"] == DATA_1[2] and first["state"] == "Karnataka" and first["mp_name"] == "Some MP"
    assert first["recommended_amount"] == "497185" and first["sanction_date"] == "09-Jul-2024"
    assert second["work"] == DATA_2[2] and second["sanction_date"] is None


def assert_openpyxl_cannot_open(path):
    """Documents the original failure.  Silent when openpyxl is absent or a newer version copes with the file."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            load_workbook(path, read_only=True).close()
    except TypeError as error:
        assert "Fill" in str(error) or "unexpected keyword" in str(error) or "expected" in str(error)
    except Exception:  # noqa: BLE001 - some other library behaviour: not what this test is about
        pass


@contextlib.contextmanager
def patched_zipfile(replacement):
    original = zipfile.ZipFile
    zipfile.ZipFile = replacement
    try:
        yield
    finally:
        zipfile.ZipFile = original


# ------------------------------------------------------------------- 1-2. string storage
def test_shared_string_workbook(tmp_path):
    path = write_xlsx(tmp_path / "shared.xlsx", [("Sheet1", SHEET)], string_style="shared", styles=styles_xml())
    assert_standard_content(*read(path))


def test_inline_string_workbook(tmp_path):
    path = write_xlsx(tmp_path / "inline.xlsx", [("Sheet1", SHEET)], string_style="inline")
    assert_standard_content(*read(path))


def test_formula_string_cells_use_their_cached_value(tmp_path):
    path = write_xlsx(tmp_path / "formula.xlsx", [("Sheet1", SHEET)], string_style="formula")
    assert_standard_content(*read(path))


def test_rich_text_is_concatenated_and_phonetic_hints_are_skipped(tmp_path):
    rich = Rich(["Hello ", "World"], phonetic="hint-that-is-not-part-of-the-text")
    for style in ("shared", "inline"):
        path = write_xlsx(tmp_path / f"rich_{style}.xlsx", [("Sheet1", [TITLE, HEADER, with_cells(state=rich)])], string_style=style)
        _, data = read(path)
        assert data[0][1]["state"] == "Hello World"


def test_excel_character_escapes_are_decoded(tmp_path):
    raw = [with_cells(state="a_x000A_b", ida="_x005F_x0041_", amount="lone_xD800_surrogate")]
    for style in ("shared", "inline", "formula"):
        path = write_xlsx(tmp_path / f"esc_{style}.xlsx", [("Sheet1", [TITLE, HEADER, *raw])], string_style=style)
        _, data = read(path)
        record = data[0][1]
        assert record["state"] == "a\nb"                       # _x000A_ is a newline
        assert record["ida"] == "_x0041_"                      # _x005F_ is a literal underscore: no second decoding
        assert record["recommended_amount"] == "lone\ufffdsurrogate"   # unencodable surrogate -> U+FFFD


# ------------------------------------------------------------------- 3-4. numbers, booleans
def test_numeric_cells_keep_their_numeric_type(tmp_path):
    rows = [
        TITLE, HEADER,
        with_cells(amount=497185, sanction=45482),                         # ints (45482 = 09-Jul-2024 as a serial)
        with_cells(amount=154773472.11, sanction=Raw('<c{ref}><v>1.5E+3</v></c>')),
        with_cells(amount=-5, sanction=0.5),
        with_cells(amount="0012"),                                          # numeric-looking TEXT stays text
    ]
    _, data = read(write_xlsx(tmp_path / "numbers.xlsx", [("Sheet1", rows)]))
    values = [(r["recommended_amount"], r["sanction_date"]) for _, r in data]
    assert values[0] == (497185, 45482) and type(values[0][0]) is int
    assert values[1] == (154773472.11, 1500.0) and type(values[1][0]) is float
    assert values[2] == (-5, 0.5)
    assert values[3][0] == "0012" and type(values[3][0]) is str


def test_boolean_cells(tmp_path):
    rows = [TITLE, HEADER, with_cells(state=True, ida=False)]
    _, data = read(write_xlsx(tmp_path / "bool.xlsx", [("Sheet1", rows)]))
    record = data[0][1]
    assert record["state"] is True and record["ida"] is False


def test_numeric_cells_flow_into_the_parsers(tmp_path):
    from data_pipeline.parsing import parse_amount, parse_date

    rows = [TITLE, HEADER, with_cells(amount=497185, sanction=45482)]
    _, data = read(write_xlsx(tmp_path / "flow.xlsx", [("Sheet1", rows)]))
    record = data[0][1]
    assert str(parse_amount(record["recommended_amount"]).value) == "497185"
    assert parse_date(record["sanction_date"]).value == dt.date(2024, 7, 9)


# ------------------------------------------------------------- 5. cell positions / coordinates
def test_cells_without_coordinates_are_placed_sequentially(tmp_path):
    path = write_xlsx(tmp_path / "nocoord.xlsx", [("Sheet1", SHEET)], coordinates=False, row_numbers=False, keep_empty=True)
    assert_standard_content(*read(path))


def test_rows_may_lack_row_numbers_while_cells_keep_theirs(tmp_path):
    path = write_xlsx(tmp_path / "norow.xlsx", [("Sheet1", SHEET)], coordinates=True, row_numbers=False)
    assert_standard_content(*read(path))


def test_cells_are_placed_by_reference_when_out_of_order_or_sparse(tmp_path):
    # header cells written right-to-left: only the r="A2".."K2" references say where each one belongs
    header_row = [Raw(f'<c r="{chr(65 + i)}2" t="inlineStr"><is><t>{name}</t></is></c>') for i, name in enumerate(HEADER)][::-1]
    sparse = [None] * STATE + ["OnlyState"]                                 # data only in column D
    rows = [TITLE, header_row, sparse]
    layout, data = read(write_xlsx(tmp_path / "order.xlsx", [("Sheet1", rows)]))
    assert layout.header_row == 2 and layout.header_for["state"] == "State"
    record = data[0][1]
    assert record["state"] == "OnlyState" and record["work"] is None and record["sanction_date"] is None


def test_columns_beyond_z_are_resolved(tmp_path):
    wide_header = [f"filler {i}" for i in range(27)] + HEADER                # first real header sits in column AB
    row = [None] * 27 + DATA_1
    _, data = read(write_xlsx(tmp_path / "wide.xlsx", [("Sheet1", [wide_header, row])]))
    assert data[0][1]["state"] == "Karnataka" and data[0][1]["sanction_date"] == "09-Jul-2024"


def test_gaps_between_rows_come_back_as_blank_rows_so_numbers_stay_aligned(tmp_path):
    rows = [TITLE, HEADER, DATA_1, Row(6, DATA_2)]                          # rows 4 and 5 have no <row> element
    _, data = read(write_xlsx(tmp_path / "gaps.xlsx", [("Sheet1", rows)]))
    assert [n for n, _ in data] == [3, 4, 5, 6]
    assert [is_blank(rec) for _, rec in data] == [False, True, True, False]


def test_formula_and_special_cells_use_cached_values(tmp_path):
    cells = with_cells(
        state=Raw('<c{ref}><f>SUM(A1:A2)</f><v>42</v></c>'),
        ida=Raw('<c{ref} t="e"><v>#N/A</v></c>'),
        amount=Raw('<c{ref} t="d"><v>2024-07-08T00:00:00</v></c>'),
        sanction=Raw('<c{ref}><f>1+1</f></c>'),                              # formula without a cached value
    )
    _, data = read(write_xlsx(tmp_path / "special.xlsx", [("Sheet1", [TITLE, HEADER, cells])]))
    record = data[0][1]
    assert (record["state"], record["ida"], record["recommended_amount"], record["sanction_date"]) == (
        42, "#N/A", "2024-07-08T00:00:00", None)


def test_empty_strings_and_empty_cells_are_missing_values(tmp_path):
    rows = [TITLE, HEADER, with_cells(state="", ida=None)]
    _, data = read(write_xlsx(tmp_path / "empty.xlsx", [("Sheet1", rows)], keep_empty=True))
    assert data[0][1]["state"] is None and data[0][1]["ida"] is None


# --------------------------------------------------------- 6-7. several sheets, title + header
def test_other_sheets_are_listed_and_the_sheet_with_the_headers_is_read(tmp_path):
    sheets = [("Notes", [["just notes"], ["nothing to see"]]), ("Data & More", SHEET)]
    layout, data = read(write_xlsx(tmp_path / "two.xlsx", sheets))
    assert layout.sheet_name == "Data & More" and layout.other_sheets == ["Notes"]
    assert [n for n, _ in data] == [3, 4]


def test_the_first_matching_sheet_wins_and_the_rest_are_reported_not_ignored(tmp_path):
    layout, _ = read(write_xlsx(tmp_path / "parts.xlsx", [("Part1", SHEET), ("Part2", SHEET)]))
    assert layout.sheet_name == "Part1" and layout.other_sheets == ["Part2"]


def test_a_sheet_that_cannot_be_resolved_is_reported_as_unread_while_a_good_one_is_read(tmp_path):
    sheets = [("Broken", [["x"]]), ("Data", SHEET)]
    layout, data = read(write_xlsx(tmp_path / "half.xlsx", sheets, break_package="missing_target"))
    assert layout.sheet_name == "Data" and layout.other_sheets == ["Broken"] and len(data) == 2


def test_title_row_then_header_row_and_a_header_further_down(tmp_path):
    layout, data = read(write_xlsx(tmp_path / "title.xlsx", [("Sheet1", SHEET)]))
    assert layout.title == "Works Recommended" and layout.header_row == 2 and data[0][0] == 3
    lower = [TITLE, ["Generated on 20-Sep-2026"], Row(5, HEADER), DATA_1]
    layout, data = read(write_xlsx(tmp_path / "lower.xlsx", [("Sheet1", lower)]))
    assert layout.header_row == 5 and layout.title == "Works Recommended" and data[0][0] == 6


def test_the_workbook_uses_the_1904_date_system_flag(tmp_path):
    plain, _ = read(write_xlsx(tmp_path / "a.xlsx", [("Sheet1", SHEET)]))
    flagged, _ = read(write_xlsx(tmp_path / "b.xlsx", [("Sheet1", SHEET)], date1904=True))
    assert plain.date1904 is False and flagged.date1904 is True


@pytest.mark.parametrize("options", [{"prefix": "x"}, {"strict": True}, {"prefix": "x", "strict": True, "string_style": "shared"}],
                         ids=["prefixed elements", "strict OOXML namespace", "prefixed strict shared strings"])
def test_namespace_variants(tmp_path, options):
    assert_standard_content(*read(write_xlsx(tmp_path / "ns.xlsx", [("Sheet1", SHEET)], **options)))


# ----------------------------------------------------------------- 8-10. malformed packages
@pytest.mark.parametrize(
    "content",
    [b"this is plain text, not a zip", b"", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64],
    ids=["text", "empty file", "old OLE .xls / encrypted"],
)
def test_a_file_that_is_not_a_zip_gives_a_clear_error(tmp_path, content):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(content)
    with pytest.raises(SchemaError, match="broken.xlsx: not a valid .xlsx"):
        read(path)


def test_a_truncated_package_gives_a_clear_error(tmp_path):
    good = write_xlsx(tmp_path / "good.xlsx", [("Sheet1", SHEET)])
    cut = tmp_path / "cut.xlsx"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    with pytest.raises(SchemaError, match="cut.xlsx"):
        read(cut)


@pytest.mark.parametrize(
    "damage, fragment",
    [
        ("missing_workbook", "workbook part 'xl/workbook.xml' is missing"),
        ("missing_rels", "workbook relationships 'xl/_rels/workbook.xml.rels' are missing"),
        ("unknown_rid", "relationship id 'rId99' not found"),
        ("missing_rid", "relationship id None not found"),
        ("missing_target", "is not in the package"),
        ("wrong_type", "not a worksheet"),
        ("bad_workbook_xml", "is not valid XML"),
        ("bad_rels_xml", "is not valid XML"),
        ("no_sheets", "lists no sheets"),
    ],
)
def test_broken_workbook_relationships_give_a_clear_error(tmp_path, damage, fragment):
    path = write_xlsx(tmp_path / "damaged.xlsx", [("Sheet1", SHEET)], break_package=damage)
    with pytest.raises(SchemaError) as caught:
        read(path)
    message = str(caught.value)
    assert message.startswith("damaged.xlsx") and fragment in message, message


def test_a_worksheet_that_is_cut_off_mid_file_raises_when_the_bad_part_is_reached(tmp_path):
    rows = [TITLE, HEADER, *[with_cells(amount=str(i)) for i in range(400)]]
    good = write_xlsx(tmp_path / "good.xlsx", [("Sheet1", rows)])
    with zipfile.ZipFile(good) as zin:
        sheet = zin.read("xl/worksheets/sheet1.xml").decode("utf-8")
    cut = write_xlsx(tmp_path / "cut.xlsx", [("Sheet1", rows)], extra_parts={"xl/worksheets/sheet1.xml": sheet[: len(sheet) * 3 // 4]})
    with pytest.raises(SchemaError, match="is not valid XML"):
        read(cut)


def test_shared_string_problems_are_schema_errors(tmp_path):
    bad_index = [TITLE, HEADER, with_cells(state=Raw('<c{ref} t="s"><v>9999</v></c>'))]
    with pytest.raises(SchemaError, match="shared string index '9999' is invalid"):
        read(write_xlsx(tmp_path / "idx.xlsx", [("Sheet1", bad_index)], string_style="shared"))
    with pytest.raises(SchemaError, match="no sharedStrings part"):
        read(write_xlsx(tmp_path / "nosst.xlsx", [("Sheet1", bad_index)], string_style="inline"))


# ------------------------------------------------------------------------ 10. schema problems
def test_missing_required_headers_name_the_workbook_the_sheet_and_the_columns(tmp_path):
    headers = [h for h in HEADER if h not in ("State", "Sanction Date")]
    path = write_xlsx(tmp_path / "Works Recommended.xlsx", [("Sheet1", [TITLE, headers, DATA_1])])
    with pytest.raises(SchemaError) as caught:
        read(path)
    message = str(caught.value)
    assert message.startswith("Works Recommended.xlsx") and "sheet 'Sheet1'" in message
    assert "missing required columns" in message and "state" in message and "sanction_date" in message


def test_a_workbook_with_no_header_row_at_all_says_so(tmp_path):
    path = write_xlsx(tmp_path / "plain.xlsx", [("Sheet1", [["a", "b"], ["c", "d"]])])
    with pytest.raises(SchemaError, match="expected columns not found"):
        read(path)


# ------------------------------------------------- 11. styles that used to break openpyxl
@pytest.mark.parametrize("variant", sorted(POISON_FILLS))
def test_styles_that_make_openpyxl_fail_are_ignored(tmp_path, variant):
    """The real MPLADS workbooks made openpyxl raise ``TypeError: Fill() takes no arguments`` inside styles.xml."""
    path = write_xlsx(
        tmp_path / "poison.xlsx", [("Sheet1", SHEET)], string_style="shared",
        styles=styles_xml(POISON_FILLS[variant]), style_id_on_cells=True,
    )
    assert_openpyxl_cannot_open(path)

    opened = []

    class RecordingZip(zipfile.ZipFile):
        def open(self, name, *args, **kwargs):
            opened.append(getattr(name, "filename", name))
            return super().open(name, *args, **kwargs)

    with patched_zipfile(RecordingZip):
        layout, data = read(path)
    assert_standard_content(layout, data)
    assert "xl/styles.xml" not in opened, opened            # the styles part is never even opened
    assert "xl/worksheets/sheet1.xml" in opened


def test_pipeline_output_is_identical_when_every_workbook_has_unparseable_styles(tmp_path, raw_dir):
    poisoned = tmp_path / "poisoned_raw"
    for workbook in sorted(raw_dir.glob("*.xlsx")):
        poison_styles(workbook, poisoned / workbook.name)
    assert_openpyxl_cannot_open(next(iter(sorted(poisoned.glob("*.xlsx")))))
    outputs = {}
    for label, source in (("plain", raw_dir), ("poisoned", poisoned)):
        out = tmp_path / label
        run_pipeline(PipelineConfig(raw_dir=source, output_root=out, as_of=AS_OF, demo_size=2, demo_seed="s", progress_every=1000))
        outputs[label] = out
    names = sorted(p.name for p in (outputs["plain"] / "data" / "processed").glob("*.csv"))
    assert names
    for name in names:
        plain = (outputs["plain"] / "data" / "processed" / name).read_bytes()
        assert plain == (outputs["poisoned"] / "data" / "processed" / name).read_bytes(), name
    report = (outputs["poisoned"] / "docs" / "DATA_QUALITY_REPORT.md").read_text(encoding="utf-8")
    assert "standard library (zipfile + xml.etree streaming; styles ignored)" in report


# --------------------------------------------------------------------- streaming behaviour
def big_sheet(tmp_path, count):
    body = [[str(i), "Normal/Others", f"WS/MP1/2024-2025/{i}-Road work", "Karnataka", "IDA", "Some MP", "DHARWAD",
             "A fairly long work description to make the XML bigger", "08-Jul-2024", str(1000 + i), "09-Jul-2024"]
            for i in range(1, count + 1)]
    return write_xlsx(tmp_path / "big.xlsx", [("Sheet1", [TITLE, HEADER, *body])])


def test_reading_a_few_rows_does_not_read_the_whole_worksheet(tmp_path):
    path = big_sheet(tmp_path, 20_000)
    counted = []

    class CountingStream:
        def __init__(self, inner):
            self.inner, self.bytes_read = inner, 0
            counted.append(self)

        def read(self, size=-1):
            data = self.inner.read(size)
            self.bytes_read += len(data)
            return data

        def close(self):
            self.inner.close()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    class CountingZip(zipfile.ZipFile):
        def open(self, name, *args, **kwargs):
            stream = super().open(name, *args, **kwargs)
            return CountingStream(stream) if name == "xl/worksheets/sheet1.xml" else stream

    with patched_zipfile(CountingZip):
        with SourceReader(RECOMMENDED, path) as reader:
            first = list(itertools.islice(reader.rows(), 3))
    with zipfile.ZipFile(path) as package:
        total = package.getinfo("xl/worksheets/sheet1.xml").file_size
    assert [n for n, _ in first] == [3, 4, 5]
    assert counted and counted[0].bytes_read < total * 0.05, (counted[0].bytes_read, total)


def test_memory_stays_flat_while_streaming_a_large_worksheet(tmp_path):
    path = big_sheet(tmp_path, 20_000)
    with zipfile.ZipFile(path) as package:
        size = package.getinfo("xl/worksheets/sheet1.xml").file_size
    tracemalloc.start()
    try:
        with SourceReader(RECOMMENDED, path) as reader:
            count = sum(1 for _ in reader.rows())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 20_000
    assert peak < size / 4, (peak, size)        # a parsed tree of every row would need several times the XML size


def test_the_file_handle_is_released_when_the_reader_closes(tmp_path):
    path = write_xlsx(tmp_path / "release.xlsx", [("Sheet1", SHEET)])
    with SourceReader(RECOMMENDED, path) as reader:
        next(iter(reader.rows()))
    path.unlink()                               # on Windows this fails while a handle is still open
    assert not path.exists()


# ------------------------------------------------------------------- no openpyxl at runtime
def test_production_code_does_not_import_openpyxl():
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / "data_pipeline").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n == "openpyxl" or n.startswith("openpyxl.") for n in names), f"{path.name} imports openpyxl"
    probe = "import sys, data_pipeline.build_dataset; sys.exit(1 if 'openpyxl' in sys.modules else 0)"
    done = subprocess.run([sys.executable, "-c", probe], cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr or "openpyxl was imported by the pipeline"


def test_the_whole_pipeline_runs_when_openpyxl_cannot_be_imported(tmp_path, raw_dir):
    """Every production module works without openpyxl: run the full pipeline with the import blocked."""
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "out"
    program = (
        "import sys\n"
        "sys.modules['openpyxl'] = None   # any 'import openpyxl' now raises ImportError\n"
        "from data_pipeline.build_dataset import main\n"
        f"sys.exit(main(['--raw-dir', {str(raw_dir)!r}, '--output-root', {str(out)!r}, '--as-of', '2026-09-20', '--demo-size', '0']))\n"
    )
    done = subprocess.run([sys.executable, "-c", program], cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                          capture_output=True, text=True)
    assert done.returncode == 0, (done.stdout[-800:] + done.stderr[-800:])
    assert (out / "data" / "processed" / "works_master.csv").is_file()
    assert (out / "docs" / "DATA_QUALITY_REPORT.md").is_file()
