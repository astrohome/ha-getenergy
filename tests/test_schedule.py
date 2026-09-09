"""Tests for predicting when the next statement is worth fetching.

The point of this policy is restraint: the portal is someone else's billing
system, and a fixed short interval would mean hundreds of requests a month for
data that changes once.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from getenergy.bill import Statement
from getenergy.schedule import (
    DUE_INTERVAL,
    EXPECT_AFTER_DAYS,
    MAX_SLEEP,
    MIN_SLEEP,
    UNKNOWN_INTERVAL,
    next_poll,
    statement_date,
)

LAST = date(2026, 9, 2)


def test_stays_quiet_right_after_a_statement_arrives():
    """A bill just landed; the next one is a month away."""
    assert next_poll(LAST, LAST) == MAX_SLEEP


def test_wakes_daily_once_a_new_bill_is_plausible():
    due = LAST + timedelta(days=EXPECT_AFTER_DAYS)

    assert next_poll(LAST, due) == DUE_INTERVAL
    assert next_poll(LAST, due + timedelta(days=5)) == DUE_INTERVAL


def test_sleep_shortens_as_the_expected_date_approaches():
    due = LAST + timedelta(days=EXPECT_AFTER_DAYS)
    two_days_out = due - timedelta(days=2)

    assert next_poll(LAST, two_days_out) == timedelta(days=2)


def test_never_sleeps_longer_than_the_cap():
    """Waking periodically lets a restart or missed bill self-correct."""
    assert next_poll(LAST, LAST - timedelta(days=365)) == MAX_SLEEP


def test_a_future_dated_statement_cannot_cause_a_busy_loop():
    assert next_poll(LAST, LAST - timedelta(days=1)) >= MIN_SLEEP


def test_without_a_statement_it_retries_on_a_modest_cadence():
    assert next_poll(None, LAST) == UNKNOWN_INTERVAL


def test_a_months_polling_is_a_handful_of_requests_not_hundreds():
    """Walk a full cycle and count the wake-ups.

    Twice-daily polling would be about 60; this should be closer to ten.
    """
    today, wakes = LAST, 0
    horizon = LAST + timedelta(days=31)
    while today < horizon:
        today += next_poll(LAST, today)
        wakes += 1

    assert wakes <= 12


def test_statement_date_prefers_the_printed_date():
    statement = Statement(
        period="202609",
        meta={"statement_date": "2026-09-02", "service_to": "2026-08-21"},
    )
    assert statement_date(statement) == date(2026, 9, 2)


def test_statement_date_falls_back_to_the_service_window_end():
    """Slightly earlier than the statement date, so only slightly eager."""
    statement = Statement(period="202609", meta={"service_to": "2026-08-21"})

    assert statement_date(statement) == date(2026, 8, 21)


@pytest.mark.parametrize("meta", [{}, {"statement_date": "not-a-date"}])
def test_unreadable_dates_yield_none_rather_than_raising(meta):
    assert statement_date(Statement(period="202609", meta=meta)) is None


def test_no_statement_yields_none():
    assert statement_date(None) is None
