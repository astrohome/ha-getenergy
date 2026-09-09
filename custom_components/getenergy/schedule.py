"""Decide when the next statement is worth looking for.

Bills are monthly, so polling on a fixed short interval means hundreds of
pointless requests a month against someone else's billing system. Instead the
next check is predicted from the last statement's own date.

Observed cycle on a real account: statements dated Jun 2, Jul 3, Aug 5, Sep 2
-- gaps of 31, 33 and 28 days. So the cycle is "about a month" but drifts by
several days either way, and a fixed 30-day guess would sometimes look a week
early and sometimes miss by days.

The compromise: stay quiet until the earliest plausible date, then check once a
day until the new bill appears. That is roughly one request cycle per month
plus a handful of daily checks, rather than sixty.

Pure module: no Home Assistant imports, so the policy is unit-testable.
"""

from __future__ import annotations

from datetime import date, timedelta

#: Earliest gap seen between statements, less a safety margin. Checking starts
#: here rather than at the average, so a short cycle is not missed.
EXPECT_AFTER_DAYS = 26

#: Cadence once a new statement is plausibly available.
DUE_INTERVAL = timedelta(days=1)

#: Used when there is no statement to predict from -- a first run, or a bill
#: whose date could not be parsed. Long enough not to hammer, short enough to
#: recover on its own.
UNKNOWN_INTERVAL = timedelta(hours=6)

#: Never sleep longer than this in one hop. Waking periodically lets a
#: restart, clock change or missed bill correct itself rather than leaving the
#: integration silent for weeks.
MAX_SLEEP = timedelta(days=7)

#: Floor, so a bad date can never produce a busy loop.
MIN_SLEEP = timedelta(hours=1)


def next_poll(last_statement: date | None, today: date) -> timedelta:
    """How long to wait before looking for the next statement."""
    if last_statement is None:
        return UNKNOWN_INTERVAL

    expected = last_statement + timedelta(days=EXPECT_AFTER_DAYS)

    # A statement dated in the future, or a clock skewed backwards, must not
    # translate into a very long silence.
    if today >= expected:
        return DUE_INTERVAL

    remaining = timedelta(days=(expected - today).days)
    return max(min(remaining, MAX_SLEEP), MIN_SLEEP)


def statement_date(statement) -> date | None:
    """The date printed on the statement, if it could be read.

    Falls back to the end of the service period, which is a few days earlier
    than the statement date and so only makes the next check slightly eager.
    """
    if statement is None:
        return None

    for key in ("statement_date", "service_to"):
        raw = statement.meta.get(key)
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                continue
    return None
