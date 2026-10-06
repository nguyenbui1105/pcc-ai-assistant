"""Tests for term tables and term resolution in src/schedule/fetcher.py"""
from datetime import datetime

import pytest

from src.schedule import fetcher
from src.schedule.fetcher import TERM_CODES, TERM_LABELS, TERM_NAMES, _build_term_tables, resolve_term


def test_term_codes_match_pcc_format():
    codes, names, labels = _build_term_tables(2026)
    assert codes["spring2026"] == "202602"
    assert codes["summer2026"] == "202603"
    assert codes["fall2026"] == "202604"
    assert codes["winter2027"] == "202701"
    assert codes["spring2027"] == "202702"
    assert names["winter2027"] == "winter"
    assert labels["winter2027"] == "Winter 2027"


def test_term_tables_cover_last_through_next_year():
    codes, _, _ = _build_term_tables(2026)
    assert len(codes) == 12
    assert "winter2025" in codes and "fall2027" in codes
    assert "fall2024" not in codes and "winter2028" not in codes


def test_module_tables_share_keys():
    assert TERM_CODES.keys() == TERM_NAMES.keys() == TERM_LABELS.keys()


@pytest.mark.parametrize("raw", ["fall2026", "Fall 2026", "FALL-2026", "fall_2026"])
def test_resolve_term_normalizes(raw, monkeypatch):
    monkeypatch.setitem(fetcher.TERM_CODES, "fall2026", "202604")
    assert resolve_term(raw) == "fall2026"


@pytest.mark.parametrize("raw", [None, ""])
def test_resolve_term_empty_uses_current(raw):
    assert resolve_term(raw) == fetcher.get_current_term()


def test_resolve_term_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown term"):
        resolve_term("fall1999")


@pytest.mark.parametrize("month, expected", [
    (1, "spring2026"), (3, "spring2026"),
    (4, "summer2026"), (7, "summer2026"),
    (8, "fall2026"), (12, "fall2026"),
])
def test_get_current_term_by_month(month, expected, monkeypatch):
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, month, 15)

    monkeypatch.setattr(fetcher, "datetime", _FixedDatetime)
    assert fetcher.get_current_term() == expected


def test_current_term_is_always_valid():
    assert fetcher.get_current_term() in TERM_CODES
