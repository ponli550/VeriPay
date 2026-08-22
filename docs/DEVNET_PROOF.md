# Live Devnet Proof — 2026-08-22

First live run of the chain layer. All three legs executed against
Solana devnet over a phone hotspot; both transactions independently
re-fetched via `getTransaction` and their memo contents confirmed
on-chain.

| Leg | Result | Evidence |
|---|---|---|
| Notarize audit result | confirmed, slot 486457684 | [tx BTCUMsax…](https://explorer.solana.com/tx/BTCUMsaxFJvXuaxAkXvhRJgyHvQkj44FTqpQr6vmPb1exsWtrfZFMqSmYbgHsrPckTnrS89peu6Di2KgDNFgTWq?cluster=devnet) — memo `veripay:sha256:24ba6970…fe4ae17d` |
| Pay discrepant invoice | **REFUSED** (no tx) | `PaymentBlocked: 1 failed or unverifiable check(s) — payment refused` |
| Pay verified invoice | confirmed, slot 486457695 | [tx 2unA7UT3…](https://explorer.solana.com/tx/2unA7UT3wD5tm53BZ5UjP2ysyQAXr1uJxL2FFVmySQS3rPTuiBRBKjZmnyLFWPoBvjTnYnUNXMgraRyG8nJajeX?cluster=devnet) — memo `veripay:paid:sha256:a1a9cd71…72ed7219`, recipient +1,000,000 lamports |

Reproduce: fund the payer in `.env`, then `uv run python scripts/devnet_live.py`.
The refusal leg is the demo's centrepiece: the agent held the money
because the math didn't.

## Run 2 — 2026-08-22, paid leg on a REAL document

The synthetic result dict is gone: step 4 now analyzes `clean_invoice.pdf`
**live** (DeepSeek, no fallback) and pays only because every check passed
and every quote pinned.

| Leg | Result | Evidence |
|---|---|---|
| Notarize | confirmed | [tx 5TCvCX4W…](https://explorer.solana.com/tx/5TCvCX4WjtkpuycKKNA9srmhWQd6k5e8Jj5paaBM45vYWM5Fqmtx4G9jFSAiAFsyBxzfRMftUvheiXkmRdf2jq52?cluster=devnet) |
| Discrepant sample | **REFUSED**, no tx | `1 failed or unverifiable check(s) — payment refused` |
| Clean invoice, analyzed live | **PAID**, confirmed | [tx 57duKwXN…](https://explorer.solana.com/tx/57duKwXNPMciruTe6Lm6vcMetvKgfLzxNhdpLDy38CMUXAqb4bnbs3LvT82cFai8XggJBaKkB9TFqizFz5XP3q2d?cluster=devnet) |
