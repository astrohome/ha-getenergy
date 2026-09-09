"""HTTP client for the Get Energy (UtilityNet) MyAccount portal.

The portal has no API. It is a GWT application whose only read method is
``getPortalLoader``, returning an obfuscated binary payload. What it *does*
expose cleanly is the monthly bill as a PDF, behind ordinary query parameters.
This client authenticates, discovers the account ids, and downloads
statements; parsing lives in :mod:`.bill`.

Auth is stock Java EE FORM authentication wrapped in a token-based CSRF layer.
See README.md for the full protocol write-up.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

import requests

from .const import (
    CONSOLIDATED_PDF,
    INVOICE_PDF,
    LOGIN_MARKER,
    LOGIN_POST,
    MODULE_BASE,
    PERMUTATION,
    POLICY,
    PROTECTED,
    RPC_URL,
    SERVICE,
    TOKEN_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

ACCOUNT_ID = re.compile(r"^\d{10}$")

# The login page carries a getProperty() shim naming the retailer, e.g.
#   else if(id=='retailerName')return 'GET';
RETAILER = re.compile(r"retailerName'\s*\)\s*return\s*'([^']*)'")


def qualify(username: str, retailer: str | None) -> str:
    """Return the value the portal expects in ``j_username``.

    The login form shows a plain username box, but its GWT module rewrites the
    hidden ``j_username`` field to ``<retailer>_<username>`` before submitting
    (falling back to the bare username when either part is empty). Posting the
    bare username is silently rejected as a bad credential, so this has to be
    reproduced exactly.
    """
    if not retailer or not username:
        return username
    return f"{retailer}_{username}"


class GetEnergyError(Exception):
    """Base error."""


class AuthError(GetEnergyError):
    """Credentials rejected, or the session was invalidated."""


class DocumentUnavailable(GetEnergyError):
    """The document service is temporarily unwell (503).

    Distinct from StatementNotFound on purpose. Conflating the two makes a
    single transient 503 look like "this month has no bill", which sends the
    caller walking backwards through months of PDFs it does not need.
    """


class StatementNotFound(GetEnergyError):
    """No statement exists for that period (404). Not an error condition."""


def encode_rpc(method: str) -> str:
    """Build a no-argument GWT-RPC request body.

    Verified byte-identical to what the portal's own client sends.
    """
    table = [MODULE_BASE, POLICY, SERVICE, method]
    return "|".join(["7", "0", str(len(table)), *table, "1", "2", "3", "4", "0"]) + "|"


def decode_strings(text: str) -> list[str]:
    """Return the string table of a GWT-RPC response.

    The tail of the payload is ``[..., stringTable, flags, version]``. The
    table carries every human-readable value; the preceding integer stream is
    positional and cannot be field-mapped without the obfuscated schema, so it
    is deliberately ignored.
    """
    if not text.startswith("//OK"):
        raise GetEnergyError("unexpected RPC response")
    arr = json.loads(text[4:])
    table = arr[-3]
    if not isinstance(table, list):
        raise GetEnergyError("unexpected GWT-RPC layout: no string table")
    return [s for s in table if isinstance(s, str)]


@dataclass
class GetEnergyClient:
    """Synchronous portal client. Call from an executor inside Home Assistant."""

    username: str
    password: str
    session: requests.Session = field(default_factory=requests.Session)
    _csrf: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.session.headers["User-Agent"] = USER_AGENT

    # -- auth ------------------------------------------------------------

    def login(self) -> None:
        """Run the FORM-auth handshake."""
        self.session.cookies.clear()
        self._csrf = None

        # Touching a protected resource makes the container save the request
        # and issue a session; the login page is served at that same URL.
        primer = self.session.get(PROTECTED, timeout=30)
        if LOGIN_MARKER not in primer.text:
            return

        match = RETAILER.search(primer.text)
        retailer = match.group(1) if match else None
        if retailer is None:
            _LOGGER.warning(
                "could not read the retailer prefix from the login page; "
                "submitting the bare username, which the portal usually rejects"
            )

        resp = self.session.post(
            LOGIN_POST,
            data={
                "j_username": qualify(self.username, retailer),
                "j_password": self.password,
            },
            headers={"Referer": PROTECTED},
            timeout=30,
        )
        if LOGIN_MARKER in resp.text:
            raise AuthError(
                "login rejected. Do not retry automatically - the portal locks "
                "accounts after repeated failures."
            )
        _LOGGER.debug("authenticated as %s", self.username)

    def token(self) -> str:
        """Return the session's CSRF token, still URL-encoded.

        Stable for the life of the session, so it is fetched once. It is only
        ever issued to an authenticated session: an anonymous one receives the
        matching cookie but no header.
        """
        if self._csrf:
            return self._csrf

        resp = self.session.get(
            TOKEN_URL,
            headers={"gwtservice": "true", "X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        tok = resp.headers.get("hsdsub3")
        if not tok:
            raise AuthError("no CSRF token issued - not authenticated")
        self._csrf = tok
        return tok

    # -- rpc -------------------------------------------------------------

    def account_ids(self) -> list[str]:
        """Discover account ids from the portal loader payload."""
        resp = self.session.post(
            RPC_URL,
            data=encode_rpc("getPortalLoader").encode("utf-8"),
            headers={
                "Content-Type": "text/x-gwt-rpc; charset=UTF-8",
                "X-GWT-Module-Base": MODULE_BASE,
                "X-GWT-Permutation": PERMUTATION,
                "X-Requested-With": "XMLHttpRequest",
                "gwtservice": "true",
                "hsdsub3": self.token(),
                "user": self.username,
            },
            timeout=60,
        )

        # A rejected token 403s and then invalidates the session outright, so
        # treat it as an auth failure rather than retrying in place.
        if resp.status_code == 403:
            self._csrf = None
            raise AuthError("CSRF token rejected; session invalidated")
        if LOGIN_MARKER in resp.text:
            raise AuthError("session expired during RPC")

        return sorted({s for s in decode_strings(resp.text) if ACCOUNT_ID.match(s)})

    # -- documents -------------------------------------------------------

    def _pdf(self, url: str, params: dict[str, str]) -> bytes:
        """GET a statement PDF.

        ``requests`` percent-encodes the params, which re-encodes the already
        encoded token exactly once. That double encoding is what the portal
        itself emits and what the server expects.
        """
        params = dict(
            params,
            hsdsub3=self.token(),
            # Per-request cache-buster the portal generates on each click.
            fileDownLoad=str(uuid.uuid4()).upper(),
        )
        resp = self.session.get(url, params=params, timeout=120)

        if resp.status_code == 503:
            raise DocumentUnavailable("document service unavailable (503)")
        if resp.status_code == 404:
            raise StatementNotFound("no statement for that period")
        if resp.status_code == 403:
            self._csrf = None
            raise AuthError("CSRF token rejected while downloading")
        resp.raise_for_status()

        if not resp.content.startswith(b"%PDF"):
            raise AuthError("expected a PDF; session likely expired")
        return resp.content

    def consolidated(self, period: str) -> bytes:
        """Consolidated statement across all accounts. ``period`` is YYYYMM."""
        return self._pdf(
            CONSOLIDATED_PDF.format(period=period), {"statementPeriod": period}
        )

    def invoice(self, account_id: str, statement_date: str) -> bytes:
        """Single-account invoice. ``statement_date`` is YYYYMMDD."""
        return self._pdf(
            INVOICE_PDF.format(date=statement_date),
            {"accountId": account_id, "statementDate": statement_date},
        )

    def latest_consolidated(self, months: int) -> tuple[str, bytes]:
        """Walk back month by month until a statement exists.

        Only a 404 justifies stepping back a month. A 503 is propagated
        immediately: the document service being briefly unwell says nothing
        about whether that month has a bill, and treating it as absent would
        download every month in the window for no reason.

        Returns ``(period, pdf_bytes)``. Raises :class:`StatementNotFound` if
        the window holds no statement.
        """
        today = date.today()
        year, month = today.year, today.month
        for _ in range(months):
            period = f"{year:04d}{month:02d}"
            try:
                return period, self.consolidated(period)
            except StatementNotFound:
                month -= 1
                if month == 0:
                    year, month = year - 1, 12
        raise StatementNotFound(f"no statement found in the last {months} months")
