import pytest

from data_pipeline.footer_detection import (
    NOT_FOOTER,
    classify_cheap,
    find_aggregate_match,
    representative_values,
    scan_amount_column,
)
from data_pipeline.parsing import parse_amount, parse_date
from data_pipeline.work_id import is_unkeyed_na

# ---------------------------------------------------------------------- classify_cheap
BASE_RECOMMENDED = {
    "work_category": "Normal/Others", "state": "Karnataka", "ida": "SOME IDA", "mp_name": "Real MP",
    "constituency": "DHARWAD", "work_description": "A perfectly normal description", "work": "Construction of a road",
    "recommended_date": "2024-07-08", "recommended_amount": "497185", "sanction_date": "2024-07-09",
}


def parsed_for(clean: dict, date_fields=(), amount_fields=()) -> dict:
    """Build the parsed_fields dict classify_cheap expects, from already-clean text (test convenience)."""
    fields = {}
    for name in date_fields:
        fields[name] = parse_date(clean.get(name)) if clean.get(name) else parse_date(None)
    for name in amount_fields:
        fields[name] = parse_amount(clean.get(name)) if clean.get(name) else parse_amount(None)
    return fields


def test_ordinary_row_is_not_a_footer():
    clean = dict(BASE_RECOMMENDED)
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    verdict = classify_cheap("recommended", clean, clean, parsed)
    assert verdict == NOT_FOOTER
    assert not verdict


@pytest.mark.parametrize("field", ["work_category", "state", "ida", "mp_name", "constituency", "work"])
def test_grand_total_label_is_detected_in_any_label_field(field):
    clean = dict(BASE_RECOMMENDED)
    clean[field] = "Grand Total"
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    verdict = classify_cheap("recommended", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "grand_total_label"


@pytest.mark.parametrize("text", ["Grand Total", "GRAND TOTAL", "grand-total", "Grand  Total Amount", "GrandTotal".replace("Grand", "Grand ")])
def test_grand_total_label_variants(text):
    clean = dict(BASE_RECOMMENDED, work=text)
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed).reason == "grand_total_label"


@pytest.mark.parametrize("text", ["Total", "Sub Total", "Subtotal", "Sub-total", "Total Expenditure", "Total Amount"])
def test_total_label_variants_without_the_word_grand(text):
    clean = dict(BASE_RECOMMENDED, work=text)
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    verdict = classify_cheap("recommended", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "total_label"


def test_grand_total_takes_priority_over_plain_total_label():
    clean = dict(BASE_RECOMMENDED, work_category="Total", work="Grand Total")
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed).reason == "grand_total_label"


def test_total_inside_a_long_description_does_not_trigger_because_description_is_not_scanned():
    clean = dict(BASE_RECOMMENDED, work_description="Total sanitation and drainage project for the ward")
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed) == NOT_FOOTER


def test_total_mid_sentence_in_a_label_field_does_not_trigger_the_anchored_regex():
    clean = dict(BASE_RECOMMENDED, work="Renovation with a total budget of five lakh")
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed) == NOT_FOOTER


def test_numeric_value_in_a_text_identity_field_is_a_footer_row():
    clean = dict(BASE_RECOMMENDED, mp_name="42938335066.78")
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    verdict = classify_cheap("sanctioned", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "footer_row" and "mp_name" in verdict.detail


def test_numeric_value_in_a_date_field_that_fails_as_a_date_is_a_footer_row():
    record = {"sanction_date": "58633825847.91"}   # raw text: too large to be an Excel serial date
    clean = dict(BASE_RECOMMENDED, sanction_date=None)  # parse_date already failed -> clean value is None
    parsed = parsed_for(clean, (), ("recommended_amount",))
    parsed["recommended_date"] = parse_date(BASE_RECOMMENDED["recommended_date"])
    parsed["sanction_date"] = parse_date(record["sanction_date"])
    assert parsed["sanction_date"].status == "invalid"
    verdict = classify_cheap("recommended", record, clean, parsed)
    assert verdict.is_footer and verdict.reason == "footer_row" and "sanction_date" in verdict.detail


def test_a_valid_excel_serial_number_in_a_date_field_is_not_a_mismatch():
    record = {"sanction_date": 45482}  # a real date, stored as an Excel serial number
    clean = dict(BASE_RECOMMENDED)
    clean["sanction_date"] = parse_date(45482).value.isoformat()
    parsed = parsed_for(clean, ("recommended_date",), ("recommended_amount",))
    parsed["sanction_date"] = parse_date(45482)
    assert parsed["sanction_date"].ok
    assert classify_cheap("recommended", record, clean, parsed) == NOT_FOOTER


def test_summary_amount_only_requires_every_identity_field_empty():
    clean = {k: None for k in BASE_RECOMMENDED}
    clean["recommended_amount"] = "58633825847.91"
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    verdict = classify_cheap("recommended", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "summary_amount_only"


def test_summary_amount_only_does_not_trigger_when_one_identity_field_survives():
    clean = {k: None for k in BASE_RECOMMENDED}
    clean["recommended_amount"] = "58633825847.91"
    clean["state"] = "Karnataka"   # one identity field still populated - a real row could look like this
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed) == NOT_FOOTER


def test_summary_amount_only_does_not_trigger_without_an_amount():
    clean = {k: None for k in BASE_RECOMMENDED}
    parsed = parsed_for(clean, ("recommended_date", "sanction_date"), ("recommended_amount",))
    assert classify_cheap("recommended", clean, clean, parsed) == NOT_FOOTER


def test_a_legitimate_na_unkeyed_row_never_reads_as_a_footer():
    """classify_cheap has no idea what an NA-* row even is - it just sees ordinary field values."""
    clean = dict(BASE_RECOMMENDED, work="NA-Construction of a new community hall, ward 12", sanction_date=None)
    parsed = parsed_for(clean, ("recommended_date",), ("recommended_amount",))
    parsed["sanction_date"] = parse_date(None)
    assert classify_cheap("recommended", clean, clean, parsed) == NOT_FOOTER


def test_allocation_and_calamity_use_their_own_field_sets():
    clean = {"state": None, "mp_name": "Grand Total", "constituency": None, "allocated_amount": "1000000"}
    parsed = parsed_for(clean, (), ("allocated_amount",))
    assert classify_cheap("allocation", clean, clean, parsed).reason == "grand_total_label"

    clean = {"calamity_type": None, "calamity_name": None, "mp_name": None, "consent_date": None, "consent_amount": "500000"}
    parsed = parsed_for(clean, ("consent_date",), ("consent_amount",))
    assert classify_cheap("calamity", clean, clean, parsed).reason == "summary_amount_only"


# ---------------------------------------------------------------- find_aggregate_match / scan
def test_find_aggregate_match_finds_the_unique_half_total_row():
    from decimal import Decimal
    rows = [(1, "100000"), (2, "150000"), (3, "250000"), (4, "500000")]  # 4's amount == sum of the other three
    assert find_aggregate_match(rows, Decimal("1000000")) == 4


def test_find_aggregate_match_requires_a_unique_match():
    from decimal import Decimal
    # rows 1 and 2 both equal half of the total (400/2=200) - a genuine tie, so nothing is guessed
    rows = [(1, "200"), (2, "200"), (3, "0")]
    assert find_aggregate_match(rows, Decimal("400")) is None


def test_find_aggregate_match_requires_the_minimum_row_count():
    from decimal import Decimal
    # only 2 candidate rows: two works that happen to cost the same must not be mistaken for a footer
    rows = [(1, "100"), (2, "100")]
    assert find_aggregate_match(rows, Decimal("200")) is None


def test_find_aggregate_match_ignores_missing_amounts():
    from decimal import Decimal
    rows = [(1, None), (2, "100"), (3, "100"), (4, "200")]
    assert find_aggregate_match(rows, Decimal("400")) == 4


def test_find_aggregate_match_skips_non_positive_totals():
    from decimal import Decimal
    assert find_aggregate_match([(1, "0"), (2, "0"), (3, "0")], Decimal("0")) is None
    assert find_aggregate_match([(1, "-100"), (2, "-100"), (3, "-200")], Decimal("-400")) is None


def test_scan_amount_column_computes_authoritative_stats():
    values = [("100",), (None,), ("-50",), ("0",), ("250.50",)]
    stats = scan_amount_column(iter(values))
    from decimal import Decimal
    assert stats.count == 4 and stats.sum == Decimal("300.50")
    assert stats.negatives == 1 and stats.zeros == 1
    assert stats.min == Decimal("-50") and stats.max == Decimal("250.50")


def test_scan_amount_column_of_nothing_is_all_zero():
    stats = scan_amount_column(iter([(None,), (None,)]))
    assert stats.count == 0 and stats.sum == 0 and stats.min is None and stats.max is None


# --------------------------------------------------------------------- representative_values
def test_representative_values_is_compact_and_readable():
    record = {"state": "Karnataka", "mp_name": None, "constituency": "DHARWAD", "work": "Grand Total",
              "work_category": None, "ida": None, "recommended_date": None, "sanction_date": None,
              "recommended_amount": "58633825847.91"}
    text = representative_values("recommended", record)
    assert "work=Grand Total" in text and "recommended_amount=58633825847.91" in text and "mp_name=" in text


def test_representative_values_truncates_long_fields():
    record = {"work": "x" * 500}
    text = representative_values("recommended", record, max_len=10)
    assert "work=xxxxxxx..." in text
    assert len(text) < 400


# ------------------------------------------------------------------------- is_unkeyed_na
@pytest.mark.parametrize(
    "raw",
    ["NA", "na", "NA-Construction of a hall", "NA - Construction", "NA Construction",
     "NA:Construction", "NA/Construction", "na-construction of a road"],
)
def test_is_unkeyed_na_matches_the_prefix_convention(raw):
    assert is_unkeyed_na(raw)


@pytest.mark.parametrize("raw", ["NATIONAL Highway widening", "NAME pending", "NAC/MP1/2024-2025/5-x", "Not Applicable", None, 123, ""])
def test_is_unkeyed_na_does_not_match_lookalikes(raw):
    assert not is_unkeyed_na(raw)
