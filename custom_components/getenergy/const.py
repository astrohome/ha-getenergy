"""Constants for the Get Energy integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "getenergy"
MANUFACTURER: Final = "Get Energy"

CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_KEEP_PDFS: Final = "keep_pdfs"

# Cost-structure coefficients. Home Assistant prices energy as
# sum(delta_kWh * price), so only the per-kWh part can live in a price entity;
# the standing charges have to be reported separately.
#
# The defaults below were fitted from ~2 years of statements on one Alberta
# microgeneration account (r2 > 0.98 on every wires component). They are a
# starting point, not a universal truth: run tools/history.py against your own
# history and set these in the integration options.
CONF_WIRES_PER_KWH: Final = "wires_per_kwh"
CONF_STANDING_PER_DAY: Final = "standing_per_day"
CONF_STANDING_PER_MONTH: Final = "standing_per_month"
CONF_GST_RATE: Final = "gst_rate"

DEFAULT_WIRES_PER_KWH: Final = 0.10025
DEFAULT_STANDING_PER_DAY: Final = 1.140
DEFAULT_STANDING_PER_MONTH: Final = 10.22
# GST is charged on the subtotal, which is already net of the microgen credit,
# so the same rate applies to both the import price and the export credit.
DEFAULT_GST_RATE: Final = 0.05

# How far back a routine refresh will look for the newest statement. Small on
# purpose: the backfill service is the tool for reaching further back, and a
# routine refresh should not be able to download a year of PDFs. Scheduling
# itself lives in schedule.py, which predicts the next check from the last
# statement's date instead of polling on a fixed interval.
ROUTINE_LOOKBACK_MONTHS: Final = 2

# How many months back to look for a statement on first run.
BACKFILL_MONTHS: Final = 24

BASE: Final = "https://secure.getenergy.ca"
MODULE_BASE: Final = f"{BASE}/myretailer/portal/"
PROTECTED: Final = f"{MODULE_BASE}index.html"
LOGIN_POST: Final = f"{MODULE_BASE}j_security_check"
TOKEN_URL: Final = f"{BASE}/myretailer/secure/login.html?tknr=true"
RPC_URL: Final = f"{MODULE_BASE}gwtservice"

DOCS: Final = f"{BASE}/myretailer/docs/portal"
CONSOLIDATED_PDF: Final = f"{DOCS}/consolidated/consolidated{{period}}.pdf"
INVOICE_PDF: Final = f"{DOCS}/invoice/invoice{{date}}.pdf"

SERVICE: Final = "net.utilitynet.myretailer.gwt.client.rpc.MyAccountService"

# Baked into the compiled GWT app; both change when the vendor redeploys.
POLICY: Final = "ED1C83BD02E38E518A4DBA0161C908FD"
PERMUTATION: Final = "F49F596E722D6F9BFA371A888AC8BE62"

# Present on <html> whenever the container wants credentials.
LOGIN_MARKER: Final = 'data-id="loginRequested"'

USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
