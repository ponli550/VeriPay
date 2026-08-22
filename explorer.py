"""Read-only wallet audit — paste any address, see its on-chain activity.

Public chain data needs no permission and no wallet connection: an
address is enough. Renders any wallet's recent transactions and decodes
our own veripay:* memos, so an agent's payment decisions are publicly,
independently auditable — including by people who have never used this
app. No keys, no custody, no accounts; the cache is process-memory and
dies with the process, by design.
"""

import os
import re
import time

import httpx

import screening

# Pure-python address validation: base58 alphabet, 32-44 chars — the
# shape of an ed25519 pubkey. Full curve checks live with the signer,
# which never runs in a cloud deployment.
_PUBKEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

RPC_URLS = {
    "devnet": os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com"),
    "mainnet": "https://api.mainnet-beta.solana.com",
}

# Program accounts are infrastructure, not counterparties — a memo-only
# tx would otherwise show the Memo program as "who the money moved with".
KNOWN_PROGRAM_IDS = frozenset({
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",   # Memo v2
    "11111111111111111111111111111111",                # System program
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
})

_MEMO_RE = re.compile(r'Memo \(len \d+\): "(.*)"')
_CACHE_TTL = 60
_CACHE_CAP = 50
_cache: dict = {}  # (address, network) -> (fetched_at, result)


def _rpc(method, params, network, transport=None):
    if transport is not None:
        return transport(method, params)
    r = httpx.post(RPC_URLS[network],
                   json={"jsonrpc": "2.0", "id": 1, "method": method,
                         "params": params}, timeout=25).json()
    if "error" in r:
        raise RuntimeError(str(r["error"]))
    return r["result"]


def _parse_tx(tx, address):
    if not tx:
        return None
    keys = [k.get("pubkey") for k in
            tx["transaction"]["message"].get("accountKeys", [])]
    delta = 0
    if address in keys:
        i = keys.index(address)
        pre = tx["meta"].get("preBalances", [])
        post = tx["meta"].get("postBalances", [])
        if i < len(pre) and i < len(post):
            delta = post[i] - pre[i]
    memo = ""
    for log in tx["meta"].get("logMessages") or []:
        m = _MEMO_RE.search(log)
        if m:
            memo = m.group(1)
            break
    counterparty = next((k for k in keys if k and k != address
                     and k not in KNOWN_PROGRAM_IDS), "")
    return {
        "time": tx.get("blockTime"),
        "delta": delta,
        "memo": memo,
        "veripay": memo.startswith("veripay:"),
        "counterparty": counterparty,
        "err": tx["meta"].get("err") is not None,
    }


def fetch_activity(address: str, limit: int = 12, network: str = "devnet",
                   transport=None) -> dict:
    if not _PUBKEY_RE.fullmatch(address or ""):
        raise ValueError(f"not a valid Solana address: {address[:24]}")
    if network not in RPC_URLS:
        raise ValueError(f"unknown network: {network}")

    key = (address, network)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return {**hit[1], "cached": True}

    sigs = _rpc("getSignaturesForAddress",
                [address, {"limit": max(1, min(limit, 25))}],
                network, transport) or []
    txs = []
    rate_limited = False
    for s in sigs:
        tx = None
        for attempt in (0, 1):
            try:
                tx = _rpc("getTransaction",
                          [s["signature"], {"encoding": "jsonParsed",
                                            "maxSupportedTransactionVersion": 0}],
                          network, transport)
                break
            except Exception as e:
                if "429" in str(e) or "Too many" in str(e):
                    # Public RPC quota — shared cloud egress IPs trip this
                    # constantly. Show what we have and say so; a quota is
                    # a fact to display, never a 502.
                    if attempt == 0:
                        time.sleep(0.4)
                        continue
                    rate_limited = True
                    break
                raise
        row = _parse_tx(tx, address)
        if row is not None:
            row["signature"] = s["signature"]
            txs.append(row)

    for row in txs:
        row["counterparty_sanctioned"] = (
            row["counterparty"] in screening.addresses())
    result = {"address": address, "network": network, "txs": txs,
              "rate_limited": rate_limited, "requested": len(sigs),
              "sanctions": screening.check(address), "cached": False}
    _cache[key] = (time.time(), result)
    while len(_cache) > _CACHE_CAP:
        _cache.pop(next(iter(_cache)))
    return result
