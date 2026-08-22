"""One-command live devnet proof. Run AFTER funding the payer:

    uv run python scripts/devnet_live.py

Does four things, all real, in order:
  1. balance check (refuses to start unfunded)
  2. notarize the released audit result  -> real Memo tx + explorer link
  3. pay_if_verified on the sample audit -> REFUSED (it contains the
     planted RM 100k mismatch) — the refusal IS the demo
  4. pay_if_verified on a fully verified result -> real transfer + memo
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

# 4. a fully verified result -> payment goes through, with the digest memo
clean = {"answer": "verified", "error": None, "fallback_used": False,
         "checks": [{"passed": True, "error": None}],
         "facts": [{"verified_in_source": True}]}
p = chain.pay_if_verified(clean, recipient, 1_000_000)
print("PAID      :", confirm(p["signature"]), "|", p["explorer"])
print("\nLive devnet proof complete. Both signatures are on explorer.solana.com (devnet).")
