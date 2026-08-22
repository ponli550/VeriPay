"""Sanctions screening — attribution, never accusation.

One source, verified live before vendoring: the U.S. Treasury OFAC SDN
list's Solana entries, via the MIT-licensed nightly mirror
0xB10C/ofac-sanctioned-digital-currency-addresses. The vendored
snapshot ships in fixtures/ so the demo works offline; refresh() can
pull the latest and falls back to the snapshot on any failure.

Wording rules, load-bearing (test-enforced):
- A hit is reported as "listed on OFAC SDN as of <date>" — an
  attributable public record, not a character judgment.
- A miss is "no public sanctions reports found" — with 4 SOL entries
  on the entire list, absence means non-coverage, not safety.
"""

import json
import os
import time

_VENDORED = os.path.join(os.path.dirname(__file__), "fixtures",
                         "sanctioned_sol.json")
_MIRROR = ("https://raw.githubusercontent.com/0xB10C/"
           "ofac-sanctioned-digital-currency-addresses/lists/"
           "sanctioned_addresses_SOL.json")

SOURCE = "OFAC SDN (U.S. Treasury), via MIT mirror 0xB10C"

_state = {"addresses": frozenset(), "as_of": ""}


def reload_vendored() -> None:
    with open(_VENDORED) as fh:
        _state["addresses"] = frozenset(json.load(fh))
    _state["as_of"] = time.strftime(
        "%Y-%m-%d", time.localtime(os.path.getmtime(_VENDORED)))


def refresh(fetch=None) -> bool:
    """Pull the latest list; on ANY failure keep what we have. The
    vendored snapshot is the floor, never less."""
    try:
        if fetch is not None:
            data = list(fetch())
        else:
            import httpx
            data = httpx.get(_MIRROR, timeout=15).json()
        if not isinstance(data, list) or not data:
            raise ValueError("unexpected list payload")
        _state["addresses"] = frozenset(str(a) for a in data)
        _state["as_of"] = time.strftime("%Y-%m-%d")
        return True
    except Exception:
        return False


def addresses() -> frozenset:
    return _state["addresses"]


def check(address: str) -> dict:
    listed = address in _state["addresses"]
    return {
        "listed": listed,
        "source": SOURCE,
        "as_of": _state["as_of"],
        "note": ("listed on OFAC SDN as of " + _state["as_of"]) if listed
        else "no public sanctions reports found",
    }


reload_vendored()
