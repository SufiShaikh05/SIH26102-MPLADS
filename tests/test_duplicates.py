from data_pipeline.merge import resolve_duplicates, values_equal

FIELDS = ("work_category", "state", "recommended_date", "recommended_amount")


def row(source_row, **values):
    base = {"source_row": source_row, "work_category": "Normal/Others", "state": "Karnataka",
            "recommended_date": "2024-07-08", "recommended_amount": "450000"}
    base.update(values)
    return base


def test_first_occurrence_is_kept_even_when_rows_arrive_out_of_order():
    kept, findings = resolve_duplicates([row(9), row(4), row(6)], FIELDS)
    assert kept["source_row"] == 4
    assert [f.row["source_row"] for f in findings] == [6, 9]


def test_identical_repeat_is_classified_identical():
    _, findings = resolve_duplicates([row(3), row(4)], FIELDS)
    assert [(f.relation, f.differing_fields) for f in findings] == [("identical", ())]


def test_repeat_with_a_different_value_is_conflicting_and_names_the_field():
    _, findings = resolve_duplicates([row(3), row(4, recommended_amount="460000")], FIELDS)
    assert findings[0].relation == "conflicting"
    assert findings[0].differing_fields == ("recommended_amount",)


def test_case_spacing_and_punctuation_do_not_make_a_repeat_conflicting():
    _, findings = resolve_duplicates([row(3), row(4, state="KARNATAKA ", work_category="normal / others")], FIELDS)
    assert findings[0].relation == "identical"


def test_a_missing_value_versus_a_present_value_is_a_difference():
    _, findings = resolve_duplicates([row(3), row(4, recommended_date=None)], FIELDS)
    assert findings[0].differing_fields == ("recommended_date",)


def test_two_missing_values_are_equal():
    _, findings = resolve_duplicates([row(3, recommended_date=None), row(4, recommended_date="")], FIELDS)
    assert findings[0].relation == "identical"


def test_a_single_row_has_no_findings():
    kept, findings = resolve_duplicates([row(3)], FIELDS)
    assert kept["source_row"] == 3 and findings == []


def test_dates_and_amounts_compare_exactly_so_sign_is_never_lost():
    assert not values_equal("sanction_amount", "-5", "5")
    assert not values_equal("completion_date", "2024-01-02", "2024-01-20")
    assert values_equal("sanction_amount", "5", "5")
    assert values_equal("mp_name", "Shri A. B. Kumar", "shri a b kumar")
    assert not values_equal("mp_name", "A B Kumar", "A B Sharma")
