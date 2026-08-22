"""VeriPay chain layer — Solana devnet.

Two capabilities, both built on the released (verified) result dict:

  notarize(result)          -> sha256 of the result written on-chain via the
                               Memo v2 program: an immutable, timestamped
                               proof of exactly what was verified.
  pay_if_verified(result,…) -> the product thesis in one function: the agent
                               pays ONLY a fully verified invoice. A failed
                               check, a missing check, an unpinned quote, a
                               cached fallback, or an analysis error refuses
                               payment by raising PaymentBlocked — loudly,
                               never a placeholder.

RPC transport is injectable so the entire layer tests offline; the live
path hits SOLANA_RPC_URL (devnet by default). A live failure surfaces as
an exception the UI renders as a failure — this module contains no
fallback and no fabricated signatures, by design.
"""

import base64
import hashlib
import json
import os

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

# Memo v2 — verified against solscan.io/account/... and solana-program.com/docs/memo
MEMO_PROGRAM_ID = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")

RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com")


class RpcError(RuntimeError):
    pass


class PaymentBlocked(RuntimeError):
    pass


def _rpc(method: str, params: list, transport=None) -> dict:
    post = transport or (
        lambda payload: httpx.post(RPC_URL, json=payload, timeout=20).json()
    )
    resp = post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in resp:
        raise RpcError(str(resp["error"]))
    return resp["result"]


def load_keypair() -> Keypair:
    raw = os.environ.get("SOLANA_SECRET_KEY")
    if not raw:
        raise RuntimeError(
            "SOLANA_SECRET_KEY is not set. Generate a devnet keypair "
            "(see README), fund it at faucet.solana.com, put the base58 "
            "secret in .env — and never commit it."
        )
    return Keypair.from_base58_string(raw.strip())


def result_digest(result: dict) -> str:
    """Canonical sha256 of a released result: sorted keys, no whitespace —
    the same dict always hashes the same regardless of insertion order."""
    blob = json.dumps(result, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _memo_ix(kp: Keypair, text: str) -> Instruction:
    return Instruction(
        MEMO_PROGRAM_ID,
        text.encode(),
        [AccountMeta(kp.pubkey(), is_signer=True, is_writable=False)],
    )


def _send(instructions: list, kp: Keypair, transport=None) -> str:
    bh = _rpc("getLatestBlockhash", [{"commitment": "finalized"}], transport)
    blockhash = Hash.from_string(bh["value"]["blockhash"])
    msg = Message.new_with_blockhash(instructions, kp.pubkey(), blockhash)
    tx = Transaction([kp], msg, blockhash)
    encoded = base64.b64encode(bytes(tx)).decode()
    return _rpc("sendTransaction", [encoded, {"encoding": "base64"}], transport)


def notarize(result: dict, keypair: Keypair | None = None, transport=None) -> dict:
    """Anchor sha256(result) on-chain. Returns signature, digest, explorer link."""
    kp = keypair or load_keypair()
    digest = result_digest(result)
    sig = _send([_memo_ix(kp, f"veripay:sha256:{digest}")], kp, transport)
    return {
        "signature": sig,
        "digest": digest,
        "explorer": f"https://explorer.solana.com/tx/{sig}?cluster=devnet",
    }


def pay_if_verified(
    result: dict,
    recipient: str,
    lamports: int,
    keypair: Keypair | None = None,
    transport=None,
) -> dict:
    """Pay ONLY a fully verified result. Every refusal names its reason."""
    if result.get("error"):
        raise PaymentBlocked(f"analysis error — payment refused: {result['error']}")
    if result.get("fallback_used"):
        raise PaymentBlocked("cached fallback result — payment refused")
    checks = result.get("checks") or []
    if not checks:
        raise PaymentBlocked("no checks ran — unverified invoices are never paid")
    bad = [c for c in checks if c.get("error") or not c.get("passed")]
    if bad:
        raise PaymentBlocked(
            f"{len(bad)} failed or unverifiable check(s) — payment refused"
        )
    unpinned = [
        f for f in result.get("facts") or [] if not f.get("verified_in_source")
    ]
    if unpinned:
        raise PaymentBlocked(
            f"{len(unpinned)} fact(s) not pinned to source — payment refused"
        )

    kp = keypair or load_keypair()
    digest = result_digest(result)
    instructions = [
        transfer(
            TransferParams(
                from_pubkey=kp.pubkey(),
                to_pubkey=Pubkey.from_string(recipient),
                lamports=lamports,
            )
        ),
        _memo_ix(kp, f"veripay:paid:sha256:{digest}"),
    ]
    sig = _send(instructions, kp, transport)
    return {
        "signature": sig,
        "digest": digest,
        "lamports": lamports,
        "recipient": recipient,
        "explorer": f"https://explorer.solana.com/tx/{sig}?cluster=devnet",
    }
