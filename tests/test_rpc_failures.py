"""A vendor redeploy must not turn into a re-auth loop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from getenergy.api import AuthError, GetEnergyClient, StaleClientBuild
from pdf_fixture import make_pdf


class _Refused:
    status_code = 403
    text = ""


class _Session:
    """Answers every RPC with the 403 a stale build hash gets."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def post(self, *args, **kwargs):
        return _Refused()


def test_rpc_403_is_a_stale_build_not_bad_credentials():
    client = GetEnergyClient(username="u", password="p", session=_Session())
    client._csrf = "token"  # as if minted by a session that just logged in

    with pytest.raises(StaleClientBuild) as caught:
        client.account_ids()

    # An AuthError becomes ConfigEntryAuthFailed, which a correct password
    # cannot clear.
    assert not isinstance(caught.value, AuthError)
    assert client._csrf is None  # the 403 invalidated the session


def test_routine_refresh_makes_no_rpc_call():
    pytest.importorskip("homeassistant")
    from getenergy.coordinator import GetEnergyCoordinator

    calls: list[str] = []

    class Client:
        def login(self):
            calls.append("login")

        def account_ids(self):
            raise AssertionError("routine refresh must not call the GWT-RPC")

        def latest_consolidated(self, months):
            calls.append("statement")
            return "202611", make_pdf(["Amount Due $206.53"])

    data = GetEnergyCoordinator._refresh(SimpleNamespace(client=Client()))

    assert calls == ["login", "statement"]
    assert data.statement is not None
