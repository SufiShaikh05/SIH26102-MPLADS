"""
Inspect MPLADS .xlsx files without modifying them.

Usage:
    py -3.12 .\inspect_mplads_xlsx.py

The script reads only workbook XML and prints:
- sheet names
- XML-reported dimensions when available
- first 8 rows from each sheet

It writes:
    docs/DATA_SPIKE_REPORT.txt

Original XLSX files are never modified.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT = Path(__file__).resolve().parent
RAW = PROJECT / "data" / "raw"
OUT = PROJECT / "docs" / "DATA_SPIKE_REPORT.txt"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_DOCREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def col_to_num(ref: str) -> int:
    m = re.match(r"[A-Z]+", ref.upper())
    if not m:
        return 0
    n = 0
    for ch in m.group(0):
        n = n * 26 + ord(ch) - 64
    return n


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    values: list[str] = []
    for si in root:
        if lname(si.tag) != "si":
            continue
        values.append(
            "".join((t.text or "") for t in si.iter(f"{{{NS_MAIN}}}t"))
        )
    return values


def workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

    rel_targets: dict[str, str] = {}
    for rel in rels:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not rid or not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        rel_targets[rid] = target

    result: list[tuple[str, str]] = []
    sheets = wb.find(f"{{{NS_MAIN}}}sheets")
    if sheets is None:
        return result

    for sheet in sheets:
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{{{NS_DOCREL}}}id", "")
        target = rel_targets.get(rid, "")
        if target:
            result.append((name, target))
    return result


def cell_value(c: ET.Element, shared: list[str]) -> str:
    cell_type = c.attrib.get("t", "")

    if cell_type == "inlineStr":
        return "".join((t.text or "") for t in c.iter(f"{{{NS_MAIN}}}t"))

    v = c.find(f"{{{NS_MAIN}}}v")
    if v is None:
        # Formula cells can have <f> but no cached <v>.
        return ""

    raw = v.text or ""

    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw

    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"

    return raw


def inspect_sheet(
    zf: zipfile.ZipFile,
    target: str,
    shared: list[str],
    max_rows: int = 8,
) -> tuple[str | None, list[list[str]]]:
    """
    Stream the sheet XML and stop after max_rows.
    We intentionally do not clear child elements, because row parsing needs them.
    """
    dimension: str | None = None
    rows: list[list[str]] = []

    with zf.open(target) as data:
        context = ET.iterparse(data, events=("end",))

        for _, elem in context:
            tag = lname(elem.tag)

            if tag == "dimension" and dimension is None:
                dimension = elem.attrib.get("ref")

            elif tag == "row":
                cells: dict[int, str] = {}
                for child in elem:
                    if lname(child.tag) != "c":
                        continue
                    ref = child.attrib.get("r", "")
                    col = col_to_num(ref)
                    if col:
                        cells[col] = cell_value(child, shared)

                if cells:
                    max_col = max(cells)
                    row = [cells.get(c, "") for c in range(1, max_col + 1)]
                    rows.append(row)

                if len(rows) >= max_rows:
                    break

            # Do not clear child cells before their row is processed.

    return dimension, rows


def main() -> int:
    if not RAW.exists():
        print(f"ERROR: directory not found: {RAW}")
        return 1

    files = sorted(RAW.glob("*.xlsx"))
    if not files:
        print(f"ERROR: no .xlsx files found in {RAW}")
        return 1

    lines = [
        "# SIH26102 MPLADS - Data Spike Report",
        "",
        f"Raw directory: {RAW}",
        f"Workbook count: {len(files)}",
        "",
    ]

    for path in files:
        size_mib = path.stat().st_size / (1024 * 1024)

        print("\n" + "=" * 100)
        print(f"FILE: {path.name}")
        print(f"SIZE: {size_mib:.2f} MiB")

        lines.extend([
            "=" * 100,
            f"FILE: {path.name}",
            f"SIZE_MIB: {size_mib:.2f}",
        ])

        try:
            with zipfile.ZipFile(path) as zf:
                shared = load_shared_strings(zf)
                sheets = workbook_sheets(zf)

                print(f"SHEETS: {len(sheets)}")
                lines.append(f"SHEETS: {len(sheets)}")

                for sheet_name, target in sheets:
                    print(f"\n--- Sheet: {sheet_name} ---")
                    lines.append(f"  SHEET: {sheet_name}")
                    lines.append(f"  TARGET: {target}")

                    try:
                        dimension, rows = inspect_sheet(
                            zf, target, shared, max_rows=8
                        )
                    except Exception as exc:
                        print(f"ERROR reading sheet: {exc}")
                        lines.append(f"  ERROR: {exc}")
                        continue

                    print(f"DIMENSION: {dimension}")
                    print(f"ROWS CAPTURED: {len(rows)}")

                    lines.append(f"  DIMENSION: {dimension}")
                    lines.append(f"  ROWS_CAPTURED: {len(rows)}")
                    lines.append("  FIRST_ROWS:")

                    for row in rows:
                        print(row[:40])
                        lines.append("    " + repr(row[:40]))

        except zipfile.BadZipFile as exc:
            print(f"BAD XLSX ZIP: {exc}")
            lines.append(f"BAD XLSX ZIP: {exc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + "=" * 100)
    print(f"Saved: {OUT}")
    print("Original XLSX files were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
