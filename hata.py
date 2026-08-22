"""Hata (SC-Malaysia-licensed exchange) client — READ-ONLY BY CONSTRUCTION.

Fetches the API-key user's own on-chain deposit address so the
contribution card can show a live, real receiving address from a
regulated exchange account. Retrieval only:

  - The path allowlist below is the entire callable surface. Anything
    else raises ReadOnlyViolation before a request is built.
  - No spending endpoint is implemented, named, or reachable here, and
    the test suite asserts that stays true.

Auth per Hata's developer docs: every request carries a `timestamp`,
params are sorted alphabetically and rendered as compact JSON, the
canonical string is signed with HMAC-SHA256, and the request carries
`X-API-KEY` + `Signature` headers. (Confirmed against the live API —
see sign() for the canonicalization detail their PHP pseudocode left
ambiguous.)
"""

import hashlib
import hmac as hmac_lib
import json
import os
import time

import httpx

BASE_URL = os.environ.get("HATA_BASE_URL", "https://my-api.hata.io")

# The complete callable surface. Retrieval endpoints only.
ALLOWED_PATHS = frozenset({
    "/wallet/sapi/address/deposit",   # POST, returns the user's deposit address
    "/wallet/sapi/cryptolist",        # GET, network-name discovery
})


class ReadOnlyViolation(RuntimeError):
    pass


class HataError(RuntimeError):
    pass


def _keys() -> tuple[str, str]:
    key = os.environ.get("HATA_API_KEY", "").strip()
    secret = os.environ.get("HATA_API_SECRET", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "HATA_API_KEY / HATA_API_SECRET are not set. Create them at "
            "app.hata.io (security page) and put them in .env — never commit them."
        )
    return key, secret


def sign(params: dict, secret: str) -> tuple[str, str]:
    """Canonical string = compact JSON of the params with keys sorted
    alphabetically (no whitespace); signature = HMAC-SHA256 hex over it.

    Confirmed against the live API: the raw k=v&k=v-joined scheme from
    Hata's PHP pseudocode reads ambiguous and turned out wrong — every
    base URL / HTTP method combination using it was rejected with
    "invalid hash". This compact-JSON form is what the exchange verifies.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    sig = hmac_lib.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return canonical, sig


def request(path: str, params: dict, transport=None) -> dict:
    if path not in ALLOWED_PATHS:
        raise ReadOnlyViolation(
            f"{path} is not on the read-only allowlist — this module never "
            "calls spending endpoints."
        )
    key, secret = _keys()
    body = dict(params)
    body.setdefault("timestamp", int(time.time()))
    _, sig = sign(body, secret)
    headers = {"X-API-KEY": key, "Signature": sig}
    if transport is not None:
        return transport(url=BASE_URL + path, headers=headers, body=body)
    resp = httpx.post(BASE_URL + path, headers=headers, json=body, timeout=20)
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise HataError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    if resp.status_code != 200:
        raise HataError(f"HTTP {resp.status_code}: {data}")
    return data


def get_deposit_address(token_symbol: str = "SOL",
                        network_name: str = "Solana",
                        transport=None) -> dict:
    """The user's own on-chain deposit address for the asset/network.
    A receiving address — publishing it moves no funds."""
    data = request("/wallet/sapi/address/deposit",
                   {"token_symbol": token_symbol, "network_name": network_name},
                   transport=transport)
    return {
        "address": data.get("DepositAddress", ""),
        "network": data.get("Network", network_name),
        "symbol": data.get("Symbol", token_symbol),
        "tag": data.get("Tag", ""),
    }
