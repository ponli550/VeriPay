# VeriPay — Demo-Day Runbook

Operations for the Superteam pitch. Engine mechanics are shared with
FinVerify's runbook; this covers what is VeriPay-specific.

## Night before

```bash
git pull && uv sync
uv run python make_sample.py && uv run python make_clean_invoice.py
uv run python test_backend.py        # must be fully green
uv run python scripts/devnet_live.py # ONE run: 4 legs, ~4 tx fees
```

- Check devnet balance in the script output — stay above 0.5 SOL.
  **Do not loop devnet_live idly**; each run spends fees and adds
  ledger noise to the demo recipient.
- Confirm `.env` carries: deepseek key, SOLANA_SECRET_KEY,
  VERIPAY_RECIPIENT, VERIPAY_WALLET_ADDRESS (the Hata SOL deposit
  address — confirmed network: Solana).
- Verify the Hata API key is READ-scoped in app.hata.io (#11) — the
  code cannot spend, the key shouldn't be able to either.

## The demo beats, in order

1. **Document tab**: upload the discrepant sample → dashboard, mismatch.
2. **Wallet tab**: paste the recipient (`VERIPAY_RECIPIENT` pubkey) →
   ledger shows every agent payment with `veripay:paid` memos decoded
   off public RPC. Line: *"zero shared state with our backend — anyone
   can audit this."*
3. **SHARE AUDIT LINK** → QR → a judge's phone opens the same audit.
4. Paste an OFAC-listed address on mainnet
   (`42RLPACwZPx3vYYmxSueqsogfynBDqXK298EDsNoyoHi`) → SANCTIONED
   banner. Say the wording as written: *listed, attributed, dated —
   never "scammer", and a clean result is never "safe".*
5. If asked to pay live: `uv run python scripts/devnet_live.py` in a
   terminal — REFUSED + REFUSAL-NOTARIZED + PAID with explorer links.

## RPC rate limits (#7 answer, rehearsed)

Public devnet RPC 429s under parallel load. Per-address results are
cached for 60s, so re-clicks are free; if several judges hammer fresh
addresses simultaneously the honest line is: *"public RPC rate limit —
production uses a dedicated endpoint; the data is public either way."*

## Deployment map

- **Local engine** (`uv run python server.py`) — the demo driver.
- **Vercel** (veripay-viewer.vercel.app) — full engine in the cloud;
  wallet audit and shared boards work from any device. The chain
  SIGNER is not there, by design — live payments only from the laptop.
- **Workers** (veripay.nazrijz336.workers.dev) — static viewer;
  announces VIEWER MODE itself.
- Hotspot + `scripts/tunnel.sh` remain the fallback if Vercel misbehaves.

## Failure lines

- Live model call fails → amber CACHED banner; point at it: honesty is
  the feature.
- Devnet send hangs → show the previous run's explorer links from
  docs/DEVNET_PROOF.md — every claim stays verifiable without a live tx.
