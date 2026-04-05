"""Tests for the Alpha Bank TSV parser."""

from __future__ import annotations

import decimal
import textwrap
from datetime import date
from pathlib import Path

import pytest

from actual_alpha_import.parser import (
    ParseError,
    Transaction,
    _detect_delimiter,
    _find_header_row,
    _parse_date,
    _parse_greek_decimal,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Unit tests: Greek decimal parsing
# ---------------------------------------------------------------------------

class TestParseGreekDecimal:
    def test_simple_integer(self):
        assert _parse_greek_decimal("100") == decimal.Decimal("100")

    def test_decimal_comma(self):
        assert _parse_greek_decimal("45,30") == decimal.Decimal("45.30")

    def test_thousands_dot_and_decimal_comma(self):
        assert _parse_greek_decimal("1.234,56") == decimal.Decimal("1234.56")

    def test_large_number(self):
        assert _parse_greek_decimal("7.140,24") == decimal.Decimal("7140.24")

    def test_strips_whitespace(self):
        assert _parse_greek_decimal("  1.000,00  ") == decimal.Decimal("1000.00")

    def test_empty_string_raises(self):
        with pytest.raises(ParseError, match="Empty"):
            _parse_greek_decimal("")

    def test_invalid_raises(self):
        with pytest.raises(ParseError, match="Cannot parse amount"):
            _parse_greek_decimal("not-a-number")


# ---------------------------------------------------------------------------
# Unit tests: date parsing
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_standard_date(self):
        assert _parse_date("03/04/2026") == date(2026, 4, 3)

    def test_leading_zeros(self):
        assert _parse_date("01/01/2024") == date(2024, 1, 1)

    def test_strips_whitespace(self):
        assert _parse_date("  15/06/2025  ") == date(2025, 6, 15)

    def test_invalid_date_raises(self):
        with pytest.raises(ParseError, match="Cannot parse date"):
            _parse_date("not-a-date")

    def test_wrong_format_raises(self):
        with pytest.raises(ParseError):
            _parse_date("2026-04-03")


# ---------------------------------------------------------------------------
# Unit tests: header row detection
# ---------------------------------------------------------------------------

class TestFindHeaderRow:
    def test_finds_header_at_row_6(self):
        assert _find_header_row(FIXTURES / "sample.tsv", "\t") == 6

    def test_raises_when_no_header(self, tmp_path):
        f = tmp_path / "no_header.tsv"
        f.write_text("foo\tbar\nbaz\tqux\n", encoding="utf-8-sig")
        with pytest.raises(ParseError, match="Could not find header row"):
            _find_header_row(f, "\t")


class TestDetectDelimiter:
    def test_detects_tab(self):
        assert _detect_delimiter(FIXTURES / "sample.tsv") == "\t"

    def test_detects_semicolon(self, tmp_path):
        f = tmp_path / "semi.csv"
        f.write_text("a;b;c\n1;2;3\n", encoding="utf-8-sig")
        assert _detect_delimiter(f) == ";"


# ---------------------------------------------------------------------------
# Integration tests: parse_file
# ---------------------------------------------------------------------------

class TestParseFile:
    def test_returns_correct_count(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        assert len(txns) == 4

    def test_debit_is_negative(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        # Row 1: supermarket, debit
        assert txns[0].amount == decimal.Decimal("-45.30")

    def test_credit_is_positive(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        # Row 2: salary, credit
        assert txns[1].amount == decimal.Decimal("1500.00")

    def test_large_debit_amount(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        # Row 3: electricity bill
        assert txns[2].amount == decimal.Decimal("-7140.24")

    def test_dates_parsed_correctly(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        assert txns[0].date == date(2026, 4, 3)
        assert txns[1].date == date(2026, 4, 2)

    def test_payee_set_from_description(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        assert txns[0].payee == "ΑΓΟΡΑ ΣΟΥΠΕΡΜΑΡΚΕΤ ΑΒ"
        assert txns[1].payee == "ΜΙΣΘΟΣ ΑΠΡΙΛΙΟΥ"

    def test_import_id_set_from_ref(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        assert txns[0].import_id == "TXN001"
        assert txns[1].import_id == "TXN002"

    def test_branch_stored_in_notes(self):
        txns = parse_file(FIXTURES / "sample.tsv")
        assert txns[0].notes == "ΑΘΗΝΑ"
        # Row with no branch should have empty notes
        assert txns[1].notes == ""

    def test_skips_empty_rows(self, tmp_path):
        content = (
            "Α/Α\tΗμ/νία\tΑιτιολογία\tΚατάστημα\tΤοκισμός από\tΑρ. συναλλαγής\tΠοσό\tΠρόσημο ποσού\n"
            "1\t01/04/2026\tTEST\t\t\tTXN99\t10,00\tΧ\n"
            "\t\t\t\t\t\t\t\n"
            "2\t02/04/2026\tTEST2\t\t\tTXN100\t5,00\tΠ\n"
        )
        f = tmp_path / "sparse.tsv"
        f.write_bytes(content.encode("utf-8-sig"))
        txns = parse_file(f)
        assert len(txns) == 2

    def test_missing_ref_uses_composite_import_id(self, tmp_path):
        content = (
            "Α/Α\tΗμ/νία\tΑιτιολογία\tΚατάστημα\tΤοκισμός από\tΑρ. συναλλαγής\tΠοσό\tΠρόσημο ποσού\n"
            "1\t01/04/2026\tTEST\t\t\t\t10,00\tΧ\n"
        )
        f = tmp_path / "no_ref.tsv"
        f.write_bytes(content.encode("utf-8-sig"))
        txns = parse_file(f)
        assert len(txns) == 1
        assert "|" in txns[0].import_id

    def test_malformed_amount_row_is_skipped(self, tmp_path):
        content = (
            "Α/Α\tΗμ/νία\tΑιτιολογία\tΚατάστημα\tΤοκισμός από\tΑρ. συναλλαγής\tΠοσό\tΠρόσημο ποσού\n"
            "1\t01/04/2026\tGOOD\t\t\tTXN1\t10,00\tΧ\n"
            "2\t02/04/2026\tBAD\t\t\tTXN2\tN/A\tΧ\n"
            "3\t03/04/2026\tGOOD2\t\t\tTXN3\t5,00\tΠ\n"
        )
        f = tmp_path / "bad_amount.tsv"
        f.write_bytes(content.encode("utf-8-sig"))
        txns = parse_file(f)
        assert len(txns) == 2
        assert txns[0].import_id == "TXN1"
        assert txns[1].import_id == "TXN3"

    def test_malformed_date_row_is_skipped(self, tmp_path):
        content = (
            "Α/Α\tΗμ/νία\tΑιτιολογία\tΚατάστημα\tΤοκισμός από\tΑρ. συναλλαγής\tΠοσό\tΠρόσημο ποσού\n"
            "1\t01/04/2026\tGOOD\t\t\tTXN1\t10,00\tΧ\n"
            "2\tNOT_A_DATE\tBAD\t\t\tTXN2\t5,00\tΧ\n"
        )
        f = tmp_path / "bad_date.tsv"
        f.write_bytes(content.encode("utf-8-sig"))
        txns = parse_file(f)
        assert len(txns) == 1

    def test_raises_for_missing_columns(self, tmp_path):
        content = "Col1\tCol2\nval1\tval2\n"
        f = tmp_path / "bad_cols.tsv"
        f.write_bytes(content.encode("utf-8-sig"))
        with pytest.raises(ParseError, match="missing columns"):
            parse_file(f)
