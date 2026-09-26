"""Synthetic workbooks exercising every footer/summary-row signal, plus a legitimate
NA-unkeyed recommendation and a genuinely malformed ID, so the three can never be
confused with each other. All data is INVENTED - kept separate from helpers.py's
fixture so the pre-existing pipeline tests are untouched by this one.

One footer per workbook is deliberately routed through a DIFFERENT signal, so the
full signal set is exercised across the six workbooks:

    Recommended  -> grand_total_label  (the Work cell literally says "Grand Total")
    Sanctioned   -> footer_row         (the sanction total sits in work_status - the
                                         exact shape reported from the real data)
    Completed    -> invalid_record_shape (aggregate-only: disguised as an ordinary
                                         row, amount == sum of the other three)
    Expenditure  -> summary_amount_only (blank vendor/status, amount == exact sum
                                         of the other payments - the exact shape
                                         reported from the real data)
    Allocation   -> total_label        (mp_name is literally "Total")
    Calamity     -> invalid_record_shape (aggregate-only, like Completed)

Recommended also carries one legitimate NA-* unkeyed row (a real MP, state,
constituency and amount - simply no Work ID yet) and one genuinely malformed row
(unstructured garbage text), so a test can assert all three - footer, unkeyed,
malformed - land in three different files and are never confused.
"""

from __future__ import annotations

from pathlib import Path

from helpers import FILE_NAMES, HEADERS, TITLES, write_workbook

IDA = "DHARWAD(DEPUTY COMMISSIONER DHARWAR_IDA)"
MP = "Pralhad Venkatesh Joshi"
MP2 = "Another Real MP"
T_CULT = "Construction of buildings for community cultural activities"

# Canonical IDs of the invented, legitimate works.
R1, R2, R3 = "WS/MP620/2024-2025/140001", "WS/MP620/2024-2025/140002", "WS/MP620/2024-2025/140003"
S1, S2 = "WS/MP620/2024-2025/140101", "WS/MP620/2024-2025/140102"
C1, C2, C3 = "WS/MP620/2024-2025/140201", "WS/MP620/2024-2025/140202", "WS/MP620/2024-2025/140203"
E1, E2, E3 = "WS/MP620/2024-2025/140301", "WS/MP620/2024-2025/140302", "WS/MP620/2024-2025/140303"

NA_RAW = "NA-Construction of a new community hall, ward 12"          # legitimate: no ID assigned yet
MALFORMED_RAW = "Construction work pending survey, ID to follow"     # genuinely malformed: no "/" structure, no NA prefix


def recommended_rows() -> list[list[object]]:
    return [
        ["1", "Normal/Others", f"WS/MP620/2024-2025/140001-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD", "d1", "01-Jul-2024", "100000", "02-Jul-2024"],
        ["2", "Normal/Others", f"WS/MP620/2024-2025/140002-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD", "d2", "01-Jul-2024", "150000", "02-Jul-2024"],
        ["3", "Normal/Others", f"WS/MP620/2024-2025/140003-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD", "d3", "01-Jul-2024", "200000", "02-Jul-2024"],
        # legitimate: not yet assigned a Work ID - real identity fields, no total-like content anywhere
        ["4", "Normal/Others", NA_RAW, "Punjab", "SOME IDA", MP2, "FARIDKOT", "Not yet assigned an ID", "01-Sep-2026", "250000", None],
        # genuinely malformed: unstructured text, does not start with "NA" and has no "/" structure
        ["5", "Normal/Others", MALFORMED_RAW, "Punjab", "SOME IDA", MP2, "FARIDKOT", "Survey pending", "01-Sep-2026", "50000", None],
        # footer: a "Grand Total" label in the Work cell itself, everything else blank
        [None, None, "Grand Total", None, None, None, None, None, None, None, None],
    ]


def sanctioned_rows() -> list[list[object]]:
    return [
        ["1", "Normal/Others", f"WS/MP620/2024-2025/140101-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD", "d1", "01-Jul-2024", "02-Jul-2024", "497185", "Sanction"],
        ["2", "Normal/Others", f"WS/MP620/2024-2025/140102-{T_CULT}", "Karnataka", IDA, MP, "DHARWAD", "d2", "01-Jul-2024", "02-Jul-2024", "300000", "Physical Inspection"],
        # footer: the real shape reported - sanction total (497185 + 300000) sitting in work_status
        [None, None, None, None, None, None, None, None, None, None, None, "797185.00"],
    ]


def completed_rows() -> list[list[object]]:
    return [
        ["1", "Normal/Others", f"WS/MP620/2024-2025/140201-{T_CULT}", "Karnataka", IDA, "d1", MP, "DHARWAD", "N/A", "05-Sep-2024", "100000"],
        ["2", "Normal/Others", f"WS/MP620/2024-2025/140202-{T_CULT}", "Karnataka", IDA, "d2", MP, "DHARWAD", "N/A", "06-Sep-2024", "150000"],
        ["3", "Normal/Others", f"WS/MP620/2024-2025/140203-{T_CULT}", "Karnataka", IDA, "d3", MP, "DHARWAD", "N/A", "07-Sep-2024", "250000"],
        # footer: disguised as an ordinary work row - no label, no mismatch, real-looking fields -
        # only the arithmetic (amount == sum of the three rows above) gives it away
        ["4", "Normal/Others", f"WS/MP620/2024-2025/140204-{T_CULT}", "Karnataka", IDA, "d4", MP, "DHARWAD", "N/A", "08-Sep-2024", "500000"],
    ]


def expenditure_rows() -> list[list[object]]:
    return [
        ["1", "Karnataka", T_CULT, E1, IDA, MP, "DHARWAD", "01-Aug-2024", "Vendor A", "Paid", "700000"],
        ["2", "Karnataka", T_CULT, E2, IDA, MP, "DHARWAD", "05-Aug-2024", "Vendor B", "Payment In-Progress", "300000"],
        ["3", "Karnataka", T_CULT, E3, IDA, MP, "DHARWAD", "10-Aug-2024", "Vendor C", "Paid", "150000"],
        # footer: the real shape reported - blank payment status/vendor, amount == exact sum of the above
        [None, None, None, None, None, None, None, None, None, None, "1150000"],
    ]


def allocation_rows() -> list[list[object]]:
    return [
        ["1", "Karnataka", MP, "DHARWAD", "500000"],
        ["2", "Bihar", MP2, "PATNA", "500000"],
        # footer: a plain "Total" label (not "grand total") in the MP-name column
        ["3", None, "Total", None, "1000000"],
    ]


def calamity_rows() -> list[list[object]]:
    return [
        ["1", "National Calamity", "Flood 2025 in Punjab", MP, "07-Dec-2025", "700000"],
        ["2", "State Calamity", "Landslide 2025", MP2, "01-Mar-2025", "300000"],
        # footer: aggregate-only, disguised as a normal-looking consent row
        ["3", "National Calamity", "Flood 2025 in Punjab", MP, "07-Dec-2025", "1000000"],
    ]


ROWS = {
    "recommended": recommended_rows, "sanctioned": sanctioned_rows, "completed": completed_rows,
    "expenditure": expenditure_rows, "allocation": allocation_rows, "calamity": calamity_rows,
}


def build_footer_raw_dir(directory: Path) -> Path:
    for key, name in FILE_NAMES.items():
        write_workbook(directory / name, TITLES[key], HEADERS[key], ROWS[key]())
    return directory
