"""Synthetic workbooks that mimic the layout seen in DATA_SPIKE_REPORT.txt.

Every value below is INVENTED for testing. None of it is MPLADS data.
The layout (title row, header row, spelling of headers such as
``RECOMMENDED AMOUNT   ( ₹ )``, the space in ``WS/ MP620/...``) copies the spike.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from openpyxl import Workbook

HEADERS = {
    "recommended": ["Sr. No.", "Work category", "WORK", "State", "IDA", "Hon'ble Members of Parliament",
                    "Constituency", "Work description", "Recommended date", "RECOMMENDED AMOUNT   ( ₹ )", "Sanction Date"],
    "sanctioned": ["Sr. No.", "Work category", "Work", "State", "IDA", "Hon'ble Members of Parliament",
                   "Constituency", "Work description", "Recommended date", "Sanction Date", "Sanction Amount ( ₹ )", "Work Status"],
    "completed": ["Sr. No.", "Work Category", "Work", "State", "IDA", "Work Description",
                  "Hon'ble Members of Parliament", "Constituency", "Image", "Completion Date", "Amount Disbursed ( ₹ )"],
    "expenditure": ["Sr. No.", "State", "Work", "Work ID", "IDA", "Hon'ble Members of Parliament", "Constituency",
                    "Expenditure Date", "Vendor Name", "Payment Status", "Fund Disbursed Amount ( ₹ )"],
    "allocation": ["Sr. No.", "State", "Hon'ble Members of Parliaments", "Constituency", "Allocated AMOUNT ( ₹ )"],
    "calamity": ["Sr. No.", "Calamity Type", "Calamity Name", "Hon'ble Members of Parliament", "Date of Consent",
                 "Consent Amount ( ₹ )"],
}
TITLES = {
    "recommended": "Works Recommended",
    "sanctioned": "Works Sanctioned",
    "completed": "Works Completed",
    "expenditure": "Expenditure on Completed and On-going Works as on Date",
    "allocation": "Allocated Limit for Hon'ble MPs",
    "calamity": "Amount consented for Calamity",
}
FILE_NAMES = {
    "recommended": "Works Recommended.xlsx",
    "sanctioned": "Works Sanctioned.xlsx",
    "completed": "Works Completed.xlsx",
    "expenditure": "Expenditure on Completed and On-going Works as on Date.xlsx",
    "allocation": "Allocated Limit for Honble MPs.xlsx",
    "calamity": "Amount consented for Calamity.xlsx",
}

T_CULT = "Construction of buildings for community cultural activities"
T_SCHOOL = "Construction of rooms and halls in school and colleges"
T_ROAD = "Construction of roads, link roads, pathways or any other road with or without drainage system"
IDA = "DHARWAD(DEPUTY COMMISSIONER DHARWAR_IDA)"
MP = "Pralhad Venkatesh Joshi"

# Canonical IDs of the invented works
W1 = "WS/MP620/2024-2025/133166"   # in all four sources, clean
W2 = "WS/MP620/2025-2026/133167"   # R + S, conflicting mp_name, format-only state difference
W3 = "WS/MP620/2024-2025/133190"   # R (x3) + S + C + E, repeated rows, date-order problems
W4 = "WS/MP620/2024-2025/133500"   # Recommended only, but carries a Sanction Date
W5 = "WS/MP620/2025-2026/133999"   # Sanctioned only
W6 = "WS/MP620/2024-2025/133888"   # Completed only
ORPHAN = "WS/MP999/2025-2026/999999"  # Expenditure only


def recommended_rows() -> list[list[object]]:
    return [
        # Excel row 3: numeric amount and a real datetime cell, like a differently exported workbook
        ["1", "Normal/Others", f"WS/ MP620/2024-2025/133166-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Construction of Community Bhavan at Navalgund", "08-Jul-2024", 497185, dt.datetime(2024, 7, 9)],
        ["2", "Trust and Society", f"WS/ MP620/2025-2026/133167-{T_SCHOOL}", "Karnataka", IDA, MP, "DHARWAD",
         "Construction of College room", "08-Jul-2024", "500000", "18-Sep-2025"],
        [],  # Excel row 5: blank spacer row
        ["3", "Normal/Others", f"WS/ MP620/2024-2025/133190-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Community Bhavan of Society", "08-Jul-2024", "450000", "23-Sep-2024"],           # row 6 (kept)
        ["4", "Normal/Others", f"WS/ MP620/2024-2025/133190-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Community Bhavan of Society", "08-Jul-2024", "460000", "23-Sep-2024"],           # row 7: conflicting repeat
        ["5", "Normal/Others", f"WS/ MP620/2024-2025/133190-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Community Bhavan of Society", "08-Jul-2024", "450000", "23-Sep-2024"],           # row 8: identical repeat
        ["6", "Normal/Others", f"WS/ MP620/2024-2025/133500-{T_ROAD}", "Karnataka", IDA, MP, "DHARWAD",
         "Road work", "01-Oct-2024", "300000", "10-Oct-2024"],
        ["7", "Normal/Others", "Construction without an id", "Karnataka", IDA, MP, "DHARWAD",
         "Row whose Work cell has no ID", "01-Oct-2024", "1000", None],
    ]


def sanctioned_rows() -> list[list[object]]:
    return [
        ["1", "Normal/Others", f"WS/ MP620/2024-2025/133166-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Construction of Community Bhavan at Navalgund", "08-Jul-2024", "09-Jul-2024", "497185", "Physical Inspection"],
        ["2", "Trust and Society", f"WS/ MP620/2025-2026/133167-{T_SCHOOL}", "KARNATAKA", IDA, "Someone Else",
         "DHARWAD", "Construction of College room", "08-Jul-2024", "18-Sep-2025", "480000", "Sanction"],
        ["3", "Normal/Others", f"WS/ MP620/2024-2025/133190-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD",
         "Community Bhavan of Society", "08-Jul-2024", "24-Sep-2024", "450000", "Sanction"],
        ["4", "Normal/Others", f"WS/ MP620/2025-2026/133999-{T_ROAD}", "Karnataka", IDA, MP, "DHARWAD",
         "Sanctioned but never recommended", "01-Aug-2024", "02-Aug-2024", "250000", "Physical Inspection"],
        ["5", "Normal/Others", f"WS/ MP620/2024-25/133001-{T_SCHOOL}", "Karnataka", IDA, MP, "DHARWAD",
         "Bad financial year in the ID", "01-Aug-2024", "02-Aug-2024", "1000", "Sanction"],
    ]


def completed_rows() -> list[list[object]]:
    return [
        ["1", "Normal/Others", f"WS/ MP620/2024-2025/133166-{T_CULT}", "Karnataka", IDA,
         "Construction of Community Bhavan at Navalgund", MP, "DHARWAD", "N/A", "05-Sep-2024", "448127"],
        ["2", "Normal/Others", f"WS/ MP620/2024-2025/133190-{T_CULT}", "Karnataka", IDA,
         "Community Bhavan of Society", MP, "DHARWAD", "N/A", "01-Jan-2024", "500000"],
        ["3", "Normal/Others", f"WS/ MP620/2024-2025/133888-{T_ROAD}", "Karnataka", IDA,
         "Completed but never sanctioned", MP, "DHARWAD", "N/A", "05-Sep-2024", "100000"],
    ]


def _exp(work_id: str, day: str, vendor: str, status: str, amount: object, state: str = "Karnataka") -> list[object]:
    return ["1", state, T_CULT, work_id, IDA, MP, "DHARWAD", day, vendor, status, amount]


def expenditure_rows() -> list[list[object]]:
    rows = [
        _exp(W1, "01-Aug-2024", "ABC Infra", "Paid", "100000"),
        _exp(W1, "15-Aug-2024", "abc  infra", "Paid", "1,50,000"),               # same vendor, Indian grouping
        _exp(W1, "01-Sep-2024", "XYZ Works", "Payment In-Progress", "abc"),      # invalid amount
        _exp(W1, "01-Aug-2024", "ABC Infra", "Paid", "100000"),                  # exact repeat of the first payment
        _exp(W3, "01-Sep-2024", "PQR Constructions", "Paid", "400000"),          # before its sanction date
        _exp(ORPHAN, "05-Sep-2025", "Some Vendor", "Paid", "-500", state="Odisha"),
        _exp("BAD-ID", "05-Sep-2025", "Some Vendor", "Paid", "10"),
    ]
    for number, row in enumerate(rows, start=1):  # distinct Sr. No. per row; it must never matter
        row[0] = str(number)
    return rows


def allocation_rows() -> list[list[object]]:
    return [["1", "Karnataka", "PRALHAD VENKATESH JOSHI", "DHARWAD", "147000000"],
            ["2", "Bihar", "Nobody Known", "SOMEWHERE", "147000000"]]


def calamity_rows() -> list[list[object]]:
    return [["1", "National Calamity", "Flood 2025 in Punjab", "Shri Gurjeet Singh Aujla", "07-Dec-2025", "7067400"],
            ["2", "State Calamity", "Vilangad Landslides 2024", "SHAFI PARAMBIL", "01-Mar-2025", "2500000"]]


ROWS = {
    "recommended": recommended_rows,
    "sanctioned": sanctioned_rows,
    "completed": completed_rows,
    "expenditure": expenditure_rows,
    "allocation": allocation_rows,
    "calamity": calamity_rows,
}


def write_workbook(path: Path, title: str, headers: list[str], rows: list[list[object]]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append([title])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def build_raw_dir(directory: Path) -> Path:
    """Write all six synthetic workbooks under their real file names."""
    for key, name in FILE_NAMES.items():
        write_workbook(directory / name, TITLES[key], HEADERS[key], ROWS[key]())
    return directory


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
