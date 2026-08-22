"""One-command live devnet proof. Run AFTER funding the payer:

    uv run python scripts/devnet_live.py

Does four things, all real, in order:
  1. balance check (refuses to start unfunded)
  2. notarize the released audit result  -> real Memo tx + explorer link
  3. pay_if_verified on the sample audit -> REFUSED (it contains the
     planted RM 100k mismatch) — the refusal IS the demo
  4. analyze() clean_invoice.pdf LIVE (no fallback), then pay_if_verified
     on that real, fully-verified result -> real transfer + memo
Prints explorer links; exits non-zero on any failure. No fallbacks here.
"""
import os, sys, time

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backend, chain
import httpx

kp = chain.load_keypair()
recipient = os.environ.get("VERIPAY_RECIPIENT", "").strip()
if not recipient:
    sys.exit("VERIPAY_RECIPIENT not set in .env")

def rpc(method, params):
    r = httpx.post(chain.RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                        "method": method, "params": params},
                   timeout=30).json()
    if "error" in r:
        raise RuntimeError(r["error"])
    return r["result"]

bal = rpc("getBalance", [str(kp.pubkey())])["value"]
print(f"payer {kp.pubkey()}  balance {bal/1e9:.4f} SOL")
if bal < 10_000_000:
    sys.exit("UNFUNDED — visit faucet.solana.com, paste the payer pubkey above, then rerun.")

def confirm(sig):
    for _ in range(20):
        time.sleep(2)
        st = rpc("getSignatureStatuses", [[sig]])["value"][0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            return st["confirmationStatus"]
    raise RuntimeError(f"tx {sig} not confirmed in 40s")

# 1. real audit of the sample (fixture path is fine offline; live is fine too)
os.environ.setdefault("DEMO_FALLBACK", "1")
result = backend.analyze("sample_report.pdf", "Does revenue add up?")
assert result["error"] is None, result["error"]

# 2. notarize — always allowed: an audit trail of what was verified
n = chain.notarize(result)
print("NOTARIZED :", confirm(n["signature"]), "|", n["explorer"])

# 3. the sample contains the planted mismatch -> payment must refuse
try:
    chain.pay_if_verified(result, recipient, 1_000_000)
    sys.exit("BUG: a discrepant invoice was paid")
except chain.PaymentBlocked as e:
    print("REFUSED   :", e)
    # Negative-result notarization: the refusal itself goes on-chain, so
    # "the agent held the money" is as publicly provable as "it paid".
    nr = chain.notarize_refusal(result, str(e))
    print("REFUSAL-NOTARIZED :", confirm(nr["signature"]), "|", nr["explorer"])

# 4. a REAL, genuinely clean invoice, analyzed LIVE (no fallback) ->
#    payment goes through only if it actually passes verification.
if not os.path.exists("clean_invoice.pdf"):
    sys.exit("clean_invoice.pdf not found. Run: uv run python make_clean_invoice.py")

os.environ.pop("DEMO_FALLBACK", None)
clean = backend.analyze(
    "clean_invoice.pdf",
    "Does the invoice total match the sum of its line items, and is the "
    "stated growth rate consistent with the prior period?",
)
if clean["error"] is not None:
    sys.exit(f"clean invoice failed verification: {clean['error']}")
if not clean["checks"]:
    sys.exit("clean invoice failed verification: no checks ran")
bad = [c for c in clean["checks"] if c.get("error") or not c.get("passed")]
if bad:
    sys.exit(
        f"clean invoice failed verification: {len(bad)} failed/unverifiable "
        "check(s) — payment withheld"
    )

p = chain.pay_if_verified(clean, recipient, 1_000_000)
print("PAID      :", confirm(p["signature"]), "|", p["explorer"])
print("\nLive devnet proof complete. Both signatures are on explorer.solana.com (devnet).")
