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


# Modelled on a real statement's layout -- dot leaders, credit signs, the
# "Total Charges and GST" trap, and per-period charge lines. Figures are
# synthetic but internally consistent: 200.00 + 10.00 = 210.00.
BILL = """
Current Due: $210.00
Previous Charges and Payments
Previous Balance $16.00
Payments Received - Thank You
Aug 12, 2026 - Electricity . . . . . . . . . . . . . . . . -$16.00
Total Payments -$16.00
Balance Forward $0.00
Current Charges
Charges . . . . . . . . . . . . . . . . . . . . . . . . . $200.00
GST . . . . . . . . . . . . . . . . . . . . . . . . . . . . $10.00
Charges and GST $210.00
Total Charges $200.00
Total GST $10.00
Total Charges and GST $210.00
Jul 01 2026 - Jul 30 2026 Usage: 400 kWh
Charge Summary
Subtotal $200.00
Total Current Charges $210.00
Total Due $210.00
Energy Charge ( Jul 23 - Jul 31: 232.0 kWh @ $0.35 ) $81.20
Energy Charge ( Aug 01 - Aug 21: 328.0 kWh @ $0.35 ) $114.80
Microgen Credit ( Jul 23 - Jul 31: 100.0 kWh @ -$0.35 ) -$35.00
Microgen Credit ( Aug 01 - Aug 21: 184.0 kWh @ -$0.35 ) -$64.40
"""


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("amount_due", 210.00),
        ("previous_balance", 16.00),
        ("payments", 16.00),
        ("balance_forward", 0.00),
        ("charges_before_tax", 200.00),
        ("gst", 10.00),
        ("electricity_imported_kwh", 400.0),
        ("electricity_exported_kwh", 284.0),
    ],
)
def test_reads_each_figure_off_a_realistic_bill(key, expected):
    assert _field(key).search(BILL) == expected


def test_subtotal_is_not_confused_with_the_post_tax_total():
    """The separator must not stride across words.

    A permissive separator lets "Total Charges" match the value in "Total
    Charges and GST $210.00", silently reporting the post-tax total as the
    subtotal -- which is exactly what an earlier revision did.
    """
    assert _field("charges_before_tax").search(BILL) == 200.00
    assert _field("charges_before_tax").search(BILL) != _field("amount_due").search(BILL)


def test_extracted_figures_reconcile():
    charges = _field("charges_before_tax").search(BILL)
    gst = _field("gst").search(BILL)
    assert round(charges + gst, 2) == _field("amount_due").search(BILL)


def test_exported_kwh_sums_every_meter_period():
    """Solar export is split per meter read, so a single match undercounts."""
    assert _field("electricity_exported_kwh").search(BILL) == 100.0 + 184.0


def test_absent_field_reports_none_rather_than_guessing():
    assert _field("gas_consumption_gj").search(BILL) is None


def test_credit_marked_with_cr_is_negative():
    assert _field("previous_balance").search("Previous Balance 88.10 CR") == -88.10


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


COMPONENTS = """
Current Rate: Fixed 35.000 cents / kWh effective for meter reads from Apr 23 2026
Statement Date: Sep 02, 2026
Due Date: Sep 10, 2026
Energy $200.00
Green Offset Charge ( Jul 23 - Jul 31: 232.0 kWh @ $0.0185 ) $2.00
Green Offset Charge ( Aug 01 - Aug 21: 328.0 kWh @ $0.0185 ) $3.00
Microgen -$99.00
Regulated Transmission and Distribution $73.00
Transmission Charge $24.00
Transmission Riders -$1.00
Distribution Charge $49.00
Distribution Riders $1.00
Balancing Pool Charge $0.60
Municipal Fee to Example City $14.00
Admin Charge $9.00
"""


def test_parses_a_real_pdf_end_to_end():
    """Exercise pypdf extraction and label matching together, not mocked."""
    from pdf_fixture import make_pdf

    lines = (BILL + COMPONENTS).strip().splitlines()
    statement = parse(make_pdf(lines), "202609")

    assert statement.missing == ["gas_consumption_gj"]
    assert statement.values["amount_due"] == 210.00
    assert statement.values["charges_before_tax"] == 200.00
    assert statement.values["electricity_imported_kwh"] == 400.0
    assert statement.values["electricity_exported_kwh"] == 284.0


def test_extracts_the_service_period_and_rate():
    """Backfilling statistics needs the window the kWh actually cover."""
    from getenergy.bill import extract_meta

    meta = extract_meta(BILL + COMPONENTS)

    assert meta["service_from"] == "2026-07-01"
    assert meta["service_to"] == "2026-07-30"
    assert meta["service_days"] == 30
    assert meta["rate_cents_per_kwh"] == 35.0
    assert meta["rate_type"] == "fixed"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("energy_charge", 200.00),
        ("green_offset_charge", 5.00),      # summed across meter periods
        ("microgen_credit", -99.00),        # a credit stays negative
        ("transmission_and_distribution", 73.00),
        ("transmission_charge", 24.00),
        ("transmission_riders", -1.00),     # riders can go either way
        ("distribution_charge", 49.00),
        ("distribution_riders", 1.00),
        ("balancing_pool", 0.60),
        ("franchise_fee", 14.00),           # via the "Municipal Fee to <city>" label
        ("admin_charge", 9.00),
    ],
)
def test_reads_each_itemised_charge_component(key, expected):
    assert _field(key).search(BILL + COMPONENTS) == expected


def test_energy_label_does_not_swallow_the_per_period_charge_lines():
    """"Energy $200.00" is the total; "Energy Charge ( ... )" are its parts."""
    assert _field("energy_charge").search(BILL + COMPONENTS) == 200.00


def test_credit_word_does_not_flip_a_sign():
    """A loose /cr/i matches the "Cr" in "Credit" and negates wrongly."""
    from getenergy.bill import _is_credit

    assert _is_credit("Microgen Credit ( Jul 23: 100.0 kWh @ $0.35 ) $35.00") is False
    assert _is_credit("Transmission Riders -$1.26") is True
    assert _is_credit("Previous Balance 88.10 CR") is True


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
