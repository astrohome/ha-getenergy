"""Tests for the pieces that do not need Home Assistant running."""

from __future__ import annotations

import pytest

from getenergy.api import decode_strings, encode_rpc
from getenergy.bill import FIELDS, Statement, parse


# Captured verbatim from the portal's own request.
REAL_REQUEST = (
    "7|0|4|https://secure.getenergy.ca/myretailer/portal/"
    "|ED1C83BD02E38E518A4DBA0161C908FD"
    "|net.utilitynet.myretailer.gwt.client.rpc.MyAccountService"
    "|getPortalLoader|1|2|3|4|0|"
)


def test_rpc_request_matches_the_real_client_byte_for_byte():
    assert encode_rpc("getPortalLoader") == REAL_REQUEST


def test_decode_strings_reads_the_table_from_the_tail():
    assert decode_strings('//OK[3,2,1,["alpha","beta"],0,7]') == ["alpha", "beta"]


def test_decode_strings_rejects_a_non_ok_payload():
    with pytest.raises(Exception):
        decode_strings("<!DOCTYPE html><html>login page</html>")


def _field(key):
    return next(f for f in FIELDS if f.key == key)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total Amount Due $123.45", 123.45),
        ("Amount Due: 1,234.56", 1234.56),
        ("Total Amount Due ($45.00)", -45.00),
    ],
)
def test_amount_due_handles_layout_variants(text, expected):
    assert _field("amount_due").search(text) == expected


def test_credit_marked_with_cr_is_negative():
    assert _field("previous_balance").search("Previous Balance 88.10 CR") == -88.10


def test_kwh_fields_are_numbers_not_currency():
    assert _field("electricity_exported_kwh").search("Energy Exported 1000 kWh") == 1000


def test_unmatched_labels_are_reported_missing_not_guessed(monkeypatch):
    """A layout change must produce gaps, never a wrong number."""
    monkeypatch.setattr(
        "getenergy.bill.extract_text", lambda _pdf: "nothing recognisable here"
    )
    statement = parse(b"%PDF-fake", "202609")

    assert statement.values == {}
    assert set(statement.missing) == {f.key for f in FIELDS}


def test_statement_date_derives_from_period():
    assert Statement(period="202609").statement_date.isoformat() == "2026-09-01"


def test_parses_a_real_pdf_end_to_end():
    """Exercise pypdf extraction and label matching together, not mocked."""
    from pdf_fixture import make_pdf

    pdf = make_pdf(
        [
            "GET ENERGY CONSOLIDATED STATEMENT",
            "Previous Balance 100.00 CR",
            "Payments Received $100.00",
            "Current Charges $250.00",
            "Total Amount Due $250.00",
            "Energy Imported 400 kWh",
            "Energy Exported 1000 kWh",
            "Consumption 5.50 GJ",
        ]
    )
    statement = parse(pdf, "202609")

    assert statement.missing == []
    assert statement.values == {
        "amount_due": 250.00,
        "previous_balance": -100.00,   # CR means credit
        "payments": 100.00,
        "current_charges": 250.00,
        "electricity_imported_kwh": 400.0,
        "electricity_exported_kwh": 1000.0,
        "gas_consumption_gj": 5.50,
    }


# -- j_username qualification ------------------------------------------------

LOGIN_PAGE_SHIM = (
    "<script>function getProperty(id) {if(id=='moduleId')return 'login.module';"
    "else if(id=='retailerName')return 'GET';else return '';}</script>"
)


def test_retailer_prefix_is_read_from_the_login_page():
    from getenergy.api import RETAILER

    assert RETAILER.search(LOGIN_PAGE_SHIM).group(1) == "GET"


def test_username_is_qualified_with_the_retailer_prefix():
    """The portal rejects a bare username; its GWT module prefixes it."""
    from getenergy.api import qualify

    assert qualify("you@example.com", "GET") == "GET_you@example.com"


@pytest.mark.parametrize("retailer", [None, ""])
def test_username_is_left_alone_when_no_prefix_is_published(retailer):
    from getenergy.api import qualify

    assert qualify("you@example.com", retailer) == "you@example.com"


def test_empty_username_is_never_prefixed():
    from getenergy.api import qualify

    assert qualify("", "GET") == ""
