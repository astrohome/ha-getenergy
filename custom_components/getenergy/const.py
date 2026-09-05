"""Constants for the Get Energy integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "getenergy"
MANUFACTURER: Final = "Get Energy"

CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_KEEP_PDFS: Final = "keep_pdfs"

# Bills are issued monthly and meter reads are monthly, so polling harder than
# this only adds load to the portal for no new data.
UPDATE_INTERVAL: Final = timedelta(hours=12)

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
