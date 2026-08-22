# Contributing to FinVerify

Developer onboarding in the style you'd want from an API vendor: numbered,
copy-pasteable, and honest about the sharp edges.

## How to Get Running

### 🔐 1. Keys and environment

```bash
cp .env.example .env
```

- `DEEPSEEK_API_KEY` — get one at platform.deepseek.com. The legacy name
  `deepseek_api` is also accepted.
- `DEMO_FALLBACK` — **keep it `0`.** It exists so a dead network minutes
  before a stage demo swaps in one cached fixture (flagged CACHED in the
  UI, never silent). During development it must stay off so real failures
  look like real failures.
- Never commit `.env`. The repo is public and push protection is on, but
  the cheapest leak is the one you don't create.

### 📄 2. Generate the sample document

```bash
uv sync
uv run python make_sample.py
```

`sample_report.pdf` carries a deliberate RM 100,000 revenue discrepancy,
a clean expenses table, and planted PII. It is generated, never
hand-edited — the fixture quotes must match it byte-for-byte.

### ✅ 3. Run the suite before touching anything

```bash
uv run python test_backend.py   # 100 tests, zero API cost, no network
```

If this isn't green on a fresh clone, stop and fix that first.

### 🚀 4. Run the engine

```bash
uv run python server.py   # primary UI    → http://127.0.0.1:7861
uv run python app.py      # Gradio alt UI → http://127.0.0.1:7860
```

## Local API

One endpoint matters:

- `POST /api/analyze` — multipart `file` (PDF/XLSX) + form `question`.
  Streams **NDJSON**, one pipeline event per line:

  ```json
  {"stage": "redact", "status": "ok", "detail": "4 PII item(s) masked before transmission", "elapsed_ms": 3, "result": null}
  ```

  Stages run in fixed order: `parse → redact → extract → verify-math →
  verify-citations → release`. **Only the `release` event carries
  `result`.** That is the gate, and a test enforces it — nothing shown to
  a user has skipped verification.

## 🧭 Rules of this codebase

These are load-bearing, not preferences. Each one has a test.

1. **Tests ahead of code.** The failing spec is committed *red* first
   (`test: failing spec for X`), the implementation lands green in the
   next commit. CI gates every push and PR.
2. **No `eval`, ever.** Verification is three fixed operations —
   `sum`, `difference`, `percent_change`. `grep -F "eval(" backend.py`
   must return nothing.
3. **Nothing fabricated on screen.** No hardcoded model output, no fake
   telemetry counts, no invented tx hashes. If a fallback answers, it
   says so (amber CACHED banner). The test suite greps the frontend for
   known fabrications.
4. **The model never grades its own work.** Checks compare against a
   *cited fact* (`against_fact_id`) whose quote is pinned verbatim to the
   source — never against a number the model computed itself.
5. **PII rules are Malaysian-specific.** NRIC needs a valid YYMMDD date
   prefix; there is deliberately **no bare 12-digit rule** (that redacts
   company registration numbers). Bare names without a title, patronymic,
   or signature cue are a *stated* limit — never claim "zero PII leaks."
6. **Retention is the request.** Uploads are unlinked in a `finally`
   block when the stream ends. Don't add persistence.

## 💡 Tips for developers

- The cheapest debugging tool is `analyze_stream()` — iterate it in a
  REPL and read the events; `analyze()` is just its consumer.
- Adding a check operation? It goes in `verify()` as pure arithmetic on
  resolved fact values, with hostile-input cases in test section 7.
- Adding a redaction rule? It needs a positive case, a false-positive
  case (what it must NOT redact), and a counted hit in section 13.
- The frontend is one self-contained file (`web/index.html`) — no build
  step, no CDN. If you add an asset that needs the network, the demo now
  depends on venue wifi. Don't.
- DeepSeek's JSON mode requires the word "json" in the prompt; it's
  already there. Temperature is 0 on purpose.

## HTTP semantics (local server)

- **200** — pipeline ran; look at per-stage `status` and `result.error`
  for what actually happened. A caught discrepancy is a *success*.
- **422** — you forgot the `question` form field or the file part.
- **5XX** — a bug. File an issue with the traceback; do not add a
  try/except that hides it.

## Contributing flow

1. Open or claim an issue (`gh issue list`).
2. Red spec commit → green implementation commit (`Closes #N`).
3. Push; CI must pass. `main` is the demo branch — keep it shippable.
