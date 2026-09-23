"""The parsers must never raise on odd cell values: one bad cell must not kill a long run."""

import datetime as dt
import random
from decimal import Decimal

from data_pipeline.parsing import decimal_to_str, normalize_text, parse_amount, parse_date
from data_pipeline.work_id import ERROR_CODES, is_canonical_work_id, normalize_work_id, parse_work_id

ALPHABET = list("0123456789 ,.-/()+eE\u20b9\u00a0\t\n_:;'\"") + [
    "Rs", "INR", "N/A", "Jan", "Feb", "Sept", "WS", "MP", "2024", "2025", "\u2013", "\u200b", "\u0662", "\ufffd",
    "\u00c3", "\u0915", "\U0001f600", "1e9", "e-",
]
ODD_VALUES = [
    None, True, False, 0, -1, 1, 2**63, -(2**70), 10**400, 0.0, -0.0, 1e-320, 1e308, float("nan"), float("inf"),
    float("-inf"), Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("1E+999999"), b"bytes", bytearray(b"x"),
    [], {}, (1, 2), object(), dt.datetime.min, dt.datetime.max, dt.date.min, dt.time(0, 0), dt.timedelta(days=1),
    "", " ", "\x00", "\x00\x01\x02", "9" * 5000, "1" + "0" * 400, "WS/" * 2000, "\u200b" * 50, "-" * 100,
]


def random_strings(count, seed):
    rnd = random.Random(seed)
    for _ in range(count):
        yield "".join(rnd.choice(ALPHABET) for _ in range(rnd.randint(0, 14)))


def test_amount_parser_never_raises_and_only_returns_plausible_numbers():
    for value in [*ODD_VALUES, *random_strings(4000, seed=1)]:
        result = parse_amount(value)
        assert result.status in ("ok", "missing", "invalid")
        if result.ok:
            assert isinstance(result.value, Decimal) and result.value.is_finite()
            assert abs(result.value) < Decimal("1e15")
            assert len(decimal_to_str(result.value)) < 40
        else:
            assert result.value is None


def test_date_parser_never_raises_and_only_returns_dates():
    for value in [*ODD_VALUES, *random_strings(4000, seed=2)]:
        result = parse_date(value)
        assert result.status in ("ok", "missing", "invalid")
        if result.ok:
            assert isinstance(result.value, dt.date)
        else:
            assert result.value is None


def test_text_normaliser_never_raises_and_is_consistent():
    for value in [*ODD_VALUES, *random_strings(4000, seed=3)]:
        cleaned, missing = normalize_text(value)
        assert (cleaned is None) == (missing is not None)
        assert missing in (None, "empty", "placeholder")
        if cleaned is not None:
            assert cleaned == cleaned.strip() and "  " not in cleaned


def test_work_id_parser_never_raises_and_its_output_is_always_canonical():
    seeds = ["WS/MP620/2024-2025/133166-x", "ws / mp 1 / 2024 - 2025 / 7", "WS/MP1/2024-2025/"]
    values = [*ODD_VALUES, *seeds, *random_strings(4000, seed=4)]
    rnd = random.Random(5)
    for _ in range(3000):  # mutate valid-looking IDs so the interesting branches are exercised
        text = list(rnd.choice(seeds))
        for _ in range(rnd.randint(1, 3)):
            text.insert(rnd.randrange(len(text) + 1), rnd.choice(ALPHABET))
        values.append("".join(text))
    for value in values:
        for bare in (False, True):
            parsed = parse_work_id(value, bare=bare)
            if parsed.ok:
                assert is_canonical_work_id(parsed.work_id)
                assert normalize_work_id(parsed.work_id) == parsed.work_id      # idempotent
            else:
                assert parsed.error in ERROR_CODES and parsed.work_id is None
