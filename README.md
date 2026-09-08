# Get Energy for Home Assistant

[![Validate](https://github.com/astrohome/ha-getenergy/actions/workflows/validate.yml/badge.svg)](https://github.com/astrohome/ha-getenergy/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

Pulls billing and consumption data from the **Get Energy** (UtilityNet)
MyAccount portal at `secure.getenergy.ca` into Home Assistant.

Works for electricity, natural gas, and solar micro-generation accounts.

> Unofficial. Not affiliated with or endorsed by Get Energy. The portal has no
> public API, so this integration talks to it the way the web app does.

## Install

**HACS → ⋮ → Custom repositories →** add `astrohome/ha-getenergy` as an
**Integration**, install, restart Home Assistant, then
**Settings → Devices & Services → Add Integration → Get Energy**.

Or copy `custom_components/getenergy/` into your `config/custom_components/`.

## Sensors

| Sensor | Unit | Bill label |
| --- | --- | --- |
| Amount due | CAD | Current Due / Total Due |
| Previous balance | CAD | Previous Balance |
| Payments | CAD | Total Payments (as a positive amount paid) |
| Balance forward | CAD | Balance Forward |
| Charges before tax | CAD | Subtotal |
| GST | CAD | Total GST |
| Electricity imported | kWh | Usage |
| Electricity exported | kWh | Microgen Credit lines, summed |
| Gas consumption | GJ | Usage |

Bills are monthly, so the coordinator polls twice a day and the values change
once a month. A sensor whose label is absent from your statement reports
**unavailable** rather than a guess — see [Calibration](#calibration).

Gas appears only on statements that include a gas account; on an
electricity-only month that sensor is correctly unavailable.

## How it works

The portal is a GWT application. Its only read method, `getPortalLoader`,
returns a positional binary stream whose field schema lives solely in
obfuscated compiled JavaScript — and that schema changes whenever the vendor
redeploys. A positional decoder would keep parsing happily while attaching the
wrong number to the wrong sensor.

So this integration uses the RPC endpoint **only** to discover account ids
(unambiguous 10-digit strings) and reads the actual figures from the **monthly
statement PDF**, whose labels are human-facing and therefore stable.

### The auth flow

Stock Java EE FORM authentication wrapped in a token-based CSRF layer.

**1. Prime the session.** `GET /myretailer/portal/index.html`. Unauthenticated,
the container serves the login page *at the protected URL* — the standard
"saved request" pattern — and issues `JSESSIONID`, `hsdsub3` and `clUUID`. The
login page carries `data-id="loginRequested"`, which is the reliable
not-logged-in signal on every response, RPC calls included.

**2. Post credentials.** The form action is the *relative* `j_security_check`,
so it resolves against the protected URL:

```
POST /myretailer/portal/j_security_check
j_username=<email>&j_password=<password>
```

Only the `j_*` names go on the wire, and `j_username` is **not** the username
you type. The login page's GWT module rewrites the hidden field to
`<retailerName>_<username>` — `GET_you@example.com` on this portal — falling
back to the bare username only when either part is empty:

```js
d = aY();  c = SR(a4);            // typed username, getProperty('retailerName')
!H0(d) && !H0(c) ? eY(c + '_' + d) : eY(d);
```

Posting the bare username is rejected as a bad credential, indistinguishably
from a wrong password. This integration reads the prefix out of the login
page's `getProperty` shim rather than hardcoding it, so other UtilityNet
retailers work too.

On failure you get the login page back. Repeated failures may lock the account,
so this integration never retries credentials automatically — it raises a
re-auth prompt instead.

**3. Mint a CSRF token.** `GET /myretailer/secure/login.html?tknr=true` with a
`gwtservice: true` header (`tknr` = token renew). The token arrives as an
**`hsdsub3` response header** — 44 chars of base64, URL-encoded. This header is
the only way a client can obtain it: the matching cookie is `HttpOnly`, so page
JavaScript can never read it. Classic double-submit CSRF.

It is issued **only to authenticated sessions**; an anonymous session gets the
`Set-Cookie` but no header. The value is stable for the life of the session.

**4. Call the RPC endpoint.**

```
POST /myretailer/portal/gwtservice
Content-Type: text/x-gwt-rpc; charset=UTF-8
X-GWT-Module-Base: https://secure.getenergy.ca/myretailer/portal/
X-GWT-Permutation: <permutation hash>
gwtservice: true
hsdsub3: <token>
user: <email>

7|0|4|<moduleBase>|<policyHash>|...MyAccountService|getPortalLoader|1|2|3|4|0|
```

**5. Download statements.** Plain GETs — the useful part:

```
GET /myretailer/docs/portal/consolidated/consolidated{YYYYMM}.pdf
      ?statementPeriod={YYYYMM}&hsdsub3={token}&fileDownLoad={GUID}

GET /myretailer/docs/portal/invoice/invoice{YYYYMMDD}.pdf
      ?accountId={id}&statementDate={YYYYMMDD}&hsdsub3={token}&fileDownLoad={GUID}
```

The token is **URL-encoded twice** here: it arrives from the header already
encoded (`…%3D`) and encoding it into a query string encodes it again
(`…%253D`). Passing the header value straight to `requests`' `params=` does
exactly this. `fileDownLoad` is a per-request cache-buster; any fresh UUID
works. The document service intermittently answers `503` — retry, don't treat
it as an auth failure.

### The sharp edge

**A stale or malformed `hsdsub3` returns `403` and then invalidates the entire
session** — every later request comes back as the login page. Always pair a
token with the session that minted it.

### When the vendor redeploys

`X-GWT-Permutation` and the policy hash in `const.py` are baked into the
compiled app. If account discovery starts failing, refresh them from
`portal.nocache.js`. Statement parsing is unaffected — that's the point of
reading the PDF.

## Calibration

The labels in `bill.py` are validated against a real Alberta electricity
statement, but **not against every layout** — gas-only, multi-account and
other-retailer bills are unverified. If a sensor shows unavailable, see what
your bill actually says:

```bash
GETENERGY_USERNAME=you@example.com GETENERGY_PASSWORD=... \
    python tools/calibrate.py --download --text
```

It prints the extracted text and which fields matched. Widen the patterns in
`custom_components/getenergy/bill.py` and please open a PR so others benefit.

## Cost structure and backfill (in progress)

HA's energy dashboard computes `cost = Σ(Δ kWh × price)` — a pure
multiplication with **no way to express a standing charge**. An Alberta
electricity bill is not linear in kWh: the admin charge and part of the
distribution charge are fixed monthly, which is why average cost per kWh falls
the more you import.

So no single price entity can be both bill-accurate and decision-accurate.
Average pricing reconciles to the bill but spikes absurdly early in the month
and gives a useless marginal signal; marginal pricing is right for decisions
but undershoots the bill by the fixed charges.

The bill prints Transmission and Distribution as single totals and never
publishes the split. `tools/history.py` recovers it empirically:

```bash
GETENERGY_USERNAME=you@example.com GETENERGY_PASSWORD=...     python tools/history.py
```

It downloads your statement history (cached, rate-limited), then regresses each
component as `charge ≈ fixed + per_kwh·kWh + per_day·days`. The `per_kwh`
slopes are what a marginal price entity should use; `fixed` and `per_day` have
to be tracked separately.

Check the `r2` column before trusting any row — a low value means kWh and days
do not explain that component, and its split should not be rounded into a rate.
The fit refuses outright when kWh never varies, rather than inventing a
plausible-looking split.

## Development

```bash
pip install pytest pypdf requests
python -m pytest tests/ -q
```

The suite covers the GWT request encoding (asserted byte-identical to the real
client's), the `j_username` retailer prefixing, response string-table decoding,
credit-sign handling, and a full PDF parse against a fixture modelled on a real
statement — dot leaders, per-period charge lines, and the "Total Charges and
GST" trap that makes a loose separator report the post-tax total as the
subtotal.

## Credentials

Stored in Home Assistant's config entry like any other integration. Nothing is
written to this repository, and the calibration tool reads from the environment.

## Licence

MIT
