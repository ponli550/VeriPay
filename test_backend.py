"""Test suite for backend.py — zero API cost.

Run: python test_backend.py
Every test runs locally. No API key, no network, no cost — except
section 12, which deliberately removes any API key to prove the
fallback engages; it never makes a real network call either.
"""

import json
import os
import sys
import tempfile

# Ensure we can import backend from the same directory
sys.path.insert(0, os.path.dirname(__file__))
import backend

PASS, FAIL = 0, 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS {label}")
        PASS += 1
    else:
        print(f"  FAIL {label}  {detail}")
        FAIL += 1


# ── 1. PDF parsing ────────────────────────────────────────────────────────

print("\n=== 1. PDF parsing ===")
if os.path.exists("sample_report.pdf"):
    pages = backend.parse_pdf("sample_report.pdf")
    check("reads pages", len(pages) > 0, f"got {len(pages)}")
    check("text is not empty", len(pages[0][1]) > 50)
    check("contains revenue", "revenue" in pages[0][1].lower())
else:
    print("  WARN sample_report.pdf not found — run make_sample.py first")

# ── 2. XLSX parsing ───────────────────────────────────────────────────────

print("\n=== 2. XLSX parsing ===")
try:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Segment", "Amount"])
    ws.append(["Product", 1200000])
    ws.append(["Services", 1150000])
    test_path = os.path.join(tempfile.gettempdir(), "_test_finverify.xlsx")
    wb.save(test_path)
    wb.close()

    sheets = backend.parse_xlsx(test_path)
    check("reads sheets", len(sheets) == 1)
    check("text contains Product", "Product" in sheets[0][1])
    check("text contains 1200000", "1200000" in sheets[0][1])
    os.remove(test_path)
except Exception as e:
    print(f"  FAIL xlsx test crashed: {e}")
    FAIL += 1

# ── 3. File router ────────────────────────────────────────────────────────

print("\n=== 3. File router ===")
try:
    backend.parse_file("nonexistent.txt")
    check("rejects .txt", False, "should have raised")
except ValueError as e:
    check("rejects .txt", ".txt" in str(e))
except FileNotFoundError:
    check("rejects .txt", False, "should be ValueError, not FileNotFoundError")

# ── 4. PII redaction ──────────────────────────────────────────────────────

print("\n=== 4. PII redaction ===")
sample = (
    "Prepared by Ahmad Bin Ali (ahmad.ali@nusantara.com.my)\n"
    "Contact: 012-3456789 | IC: 880512-14-5533\n"
    "Also: +60123456789, test@example.org"
)
clean, n = backend.redact(sample)
check("name scrubbed", "Ahmad Bin Ali" not in clean)
check("email 1 scrubbed", "ahmad.ali@nusantara.com.my" not in clean)
check("email 2 scrubbed", "test@example.org" not in clean)
check("IC scrubbed", "880512-14-5533" not in clean)
check("phone 1 scrubbed", "012-3456789" not in clean)
check("redaction count >= 5", n >= 5, f"got {n}")
check("tokens inserted", "[EMAIL_REDACTED]" in clean)
check("non-PII preserved", "Prepared by" in clean)

# ── 5. Verification: correct math ─────────────────────────────────────────

print("\n=== 5. Verification: correct sum ===")
facts = [{"id": "f1", "value": 100}, {"id": "f2", "value": 200}]
checks = [{"description": "total", "operation": "sum",
           "operand_fact_ids": ["f1", "f2"], "expected_value": 300}]
r = backend.verify(facts, checks)
check("passes correct sum", r[0]["passed"] is True)
check("actual is 300", r[0]["actual"] == 300.0)

# ── 6. Verification: caught mismatch ──────────────────────────────────────

print("\n=== 6. Verification: mismatch ===")
facts = [{"id": "f1", "value": 1200000},
         {"id": "f2", "value": 1150000},
         {"id": "f3", "value": 300000}]
checks = [{"description": "revenue total", "operation": "sum",
           "operand_fact_ids": ["f1", "f2", "f3"],
           "expected_value": 2750000}]
r = backend.verify(facts, checks)
check("fails mismatch", r[0]["passed"] is False)
check("actual is 2650000", r[0]["actual"] == 2650000.0)
check("expected is 2750000", r[0]["expected"] == 2750000.0)

# ── 7. Verification: hostile inputs ───────────────────────────────────────

print("\n=== 7. Hostile input handling ===")
hostile = {
    "string values with commas": (
        [{"id": "f1", "value": "1,200,000"}, {"id": "f2", "value": "RM 1,150,000"}],
        [{"description": "s", "operation": "sum",
          "operand_fact_ids": ["f1", "f2"], "expected_value": "2,350,000"}],
    ),
    "missing fact id": (
        [{"id": "f1", "value": 100}],
        [{"description": "s", "operation": "sum",
          "operand_fact_ids": ["f1", "f99"], "expected_value": 200}],
    ),
    "empty operands": (
        [],
        [{"description": "s", "operation": "sum",
          "operand_fact_ids": [], "expected_value": 5}],
    ),
    "divide by zero": (
        [{"id": "f1", "value": 0}, {"id": "f2", "value": 50}],
        [{"description": "s", "operation": "percent_change",
          "operand_fact_ids": ["f1", "f2"], "expected_value": 10}],
    ),
    "unknown operation": (
        [{"id": "f1", "value": 5}],
        [{"description": "s", "operation": "integrate",
          "operand_fact_ids": ["f1"], "expected_value": 5}],
    ),
    "eval injection": (
        [{"id": "f1", "value": "__import__('os').system('echo PWNED')"}],
        [{"description": "s", "operation": "sum",
          "operand_fact_ids": ["f1"], "expected_value": 1}],
    ),
    "null everything": (None, None),
    "non-dict in checks": (
        [{"id": "f1", "value": 5}],
        ["not a dict", 42],
    ),
    "missing expected_value": (
        [{"id": "f1", "value": 5}],
        [{"description": "s", "operation": "sum",
          "operand_fact_ids": ["f1"]}],
    ),
}

for label, (f, c) in hostile.items():
    try:
        out = backend.verify(f, c)
        check(label, True)
    except Exception as e:
        check(label, False, f"CRASHED: {type(e).__name__}: {e}")

# ── 8. JSON extraction from messy model output ────────────────────────────

print("\n=== 8. JSON extraction ===")
for label, raw in [
    ("clean json", '{"answer":"x"}'),
    ("fenced", '```json\n{"answer":"x"}\n```'),
    ("preamble", 'Here you go:\n{"answer":"x"}\nHope that helps!'),
    ("double fenced", '```json\n```json\n{"answer":"x"}\n```\n```'),
]:
    try:
        parsed = backend._extract_json(raw)
        check(label, parsed.get("answer") == "x")
    except Exception as e:
        check(label, False, f"CRASHED: {e}")

# ── 9. analyze() guard rails ──────────────────────────────────────────────

print("\n=== 9. analyze() guard rails ===")
for label, args in [
    ("no file", (None, "q")),
    ("no question", ("sample_report.pdf", "")),
    ("no question spaces", ("sample_report.pdf", "   ")),
    ("bad path", ("nonexistent.pdf", "q")),
    ("unsupported type", ("data.csv", "q")),
]:
    out = backend.analyze(*args)
    check(f"{label} returns error", out.get("error") is not None,
          f"got: {out.get('error')!r}")
    check(f"{label} no crash fields", "facts" in out and "checks" in out)

# ── 10. Percent change ────────────────────────────────────────────────────

print("\n=== 10. Percent change ===")
facts = [{"id": "f1", "value": 2400000}, {"id": "f2", "value": 2750000}]
checks = [{"description": "revenue growth", "operation": "percent_change",
           "operand_fact_ids": ["f1", "f2"], "expected_value": 14.58}]
r = backend.verify(facts, checks)
check("percent_change computes", r[0]["actual"] is not None)
check("percent_change close", abs(r[0]["actual"] - 14.58) < 0.1,
      f"got {r[0]['actual']}")

# ── 11. _fact_in_source (anti-hallucination check) ────────────────────────

print("\n=== 11. _fact_in_source ===")
pages = [("Page 1", "Product revenue 1,200,000 was reported.")]
check("quote found in source",
      backend._fact_in_source({"quote": "Product revenue 1,200,000"}, pages) is True)
check("quote not found in source",
      backend._fact_in_source({"quote": "Product revenue 9,999,999"}, pages) is False)
check("empty quote fails",
      backend._fact_in_source({"quote": ""}, pages) is False)
check("missing quote key fails",
      backend._fact_in_source({}, pages) is False)

# ── 12. DEMO_FALLBACK path — the non-negotiable safety-net proof ──────────

print("\n=== 12. DEMO_FALLBACK path ===")
if os.path.exists("sample_report.pdf"):
    old_keys = {k: os.environ.pop(k, None)
                for k in ("DEEPSEEK_API_KEY", "deepseek_api", "GEMINI_API_KEY")}
    old_fallback = os.environ.get("DEMO_FALLBACK")
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        out = backend.analyze(
            "sample_report.pdf",
            "What was total revenue, and does it add up?",
        )
        check("fallback returns no error", out.get("error") is None,
              f"got: {out.get('error')!r}")
        check("fallback returns facts", len(out.get("facts") or []) > 0)
        check("fallback still verifies math", len(out.get("checks") or []) > 0)
        check("fallback is flagged, never silent",
              out.get("fallback_used") is True)
        check("fallback catches the planted mismatch",
              any(c.get("passed") is False and not c.get("error")
                  for c in out.get("checks") or []))
        check("fallback quotes verify against real source",
              all(f.get("verified_in_source") for f in out.get("facts") or []),
              f"flags: {[f.get('verified_in_source') for f in out.get('facts') or []]}")
    finally:
        for k, v in old_keys.items():
            if v is not None:
                os.environ[k] = v
        if old_fallback is not None:
            os.environ["DEMO_FALLBACK"] = old_fallback
        else:
            os.environ.pop("DEMO_FALLBACK", None)
else:
    print("  WARN sample_report.pdf not found — run make_sample.py first")

# ── 13. Name heuristics + NRIC validation (v2 hardening) ──────────────────

print("\n=== 13. Name heuristics + NRIC validation ===")
t, _ = backend.redact("Approved by Siti Nurhaliza Binti Hassan on 12 May")
check("cue + binti name scrubbed", "Siti" not in t and "Approved by" in t, t)
t, _ = backend.redact("Signed for the board: MOHD RAZAK BIN OSMAN, Director")
check("ALL-CAPS patronymic scrubbed", "RAZAK" not in t, t)
t, _ = backend.redact("Datuk Seri Wan Azizah attended the meeting")
check("honorific name scrubbed", "Azizah" not in t, t)
t, _ = backend.redact("Ramasamy A/L Muniandy holds 40,000 shares")
check("a/l patronymic scrubbed", "Ramasamy" not in t, t)
t, _ = backend.redact("Company No. 202001012345 (12 digits)")
check("company reg number NOT redacted", "202001012345" in t, t)
t, _ = backend.redact("Ref: 991331-14-5533 is an invoice, not an IC")
check("impossible-date IC NOT redacted", "991331-14-5533" in t, t)
t, _ = backend.redact("IC: 880512-14-5533")
check("valid-date IC redacted", "880512-14-5533" not in t, t)
names = backend.pdf_metadata_names("sample_report.pdf") if os.path.exists("sample_report.pdf") else []
check("metadata author fn returns list", isinstance(names, list))

# ── 14. purge_upload safety gate ──────────────────────────────────────────

print("\n=== 14. purge_upload safety gate ===")
gradio_tmp = os.path.join(tempfile.gettempdir(), "gradio")
os.makedirs(gradio_tmp, exist_ok=True)
victim = os.path.join(gradio_tmp, "_finverify_purge_test.pdf")
with open(victim, "w") as fh:
    fh.write("x")
check("deletes file inside gradio temp", backend.purge_upload(victim) is True)
check("file is actually gone", not os.path.exists(victim))
check("refuses file outside gradio temp",
      backend.purge_upload("sample_report.pdf") is False)
check("sample survives the refusal", os.path.exists("sample_report.pdf")
      if os.path.exists("sample_report.pdf") else True)
check("handles None", backend.purge_upload(None) is False)
check("handles missing path", backend.purge_upload(
    os.path.join(gradio_tmp, "never_existed.pdf")) is False)

# ── 15. Recommendation layer (#23) — written BEFORE the implementation ─────

print("\n=== 15. Recommendation layer ===")
check("prompt asks for a recommendation",
      '"recommendation"' in backend.SYSTEM_PROMPT)
with open(os.path.join(os.path.dirname(__file__), "fixtures",
                       "cached_response.json")) as fh:
    _fx = json.load(fh)
check("fixture carries a recommendation",
      bool(str(_fx.get("recommendation", "")).strip()))
if os.path.exists("sample_report.pdf"):
    _oks = {k: os.environ.pop(k, None)
            for k in ("DEEPSEEK_API_KEY", "deepseek_api", "GEMINI_API_KEY")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        _out = backend.analyze("sample_report.pdf", "Does revenue add up?")
        check("analyze surfaces the recommendation",
              bool(str(_out.get("recommendation", "")).strip()),
              f"got: {_out.get('recommendation')!r}")
    finally:
        for k, v in _oks.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)


# ── 16. DeepSeek provider swap — written BEFORE the implementation ─────────

print("\n=== 16. DeepSeek provider ===")
check("backend exposes _call_deepseek", hasattr(backend, "_call_deepseek"))
check("gemini entrypoint is gone", not hasattr(backend, "_call_gemini"))
check("default model is deepseek-chat", backend.MODEL == "deepseek-chat")
_src = open(os.path.join(os.path.dirname(__file__), "backend.py")).read()
check("no gemini references left in backend", "gemini" not in _src.lower())
_saved = {k: os.environ.pop(k, None)
          for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
os.environ.pop("DEMO_FALLBACK", None)
try:
    backend._call_deepseek([("Page 1", "x")], "q")
    check("missing key raises", False, "should have raised")
except Exception as e:
    check("missing key raises", True)
    check("error names DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY" in str(e), str(e))
finally:
    for k, v in _saved.items():
        if v is not None:
            os.environ[k] = v


# ── 17. against_fact_id — expected value must come from a cited fact ───────
# Live-call finding: the model set expected_value to its own computed sum,
# so a real discrepancy showed as PASS. Written BEFORE the fix.

print("\n=== 17. against_fact_id ===")
_facts = [{"id": "f1", "value": 1200000}, {"id": "f2", "value": 1150000},
          {"id": "f3", "value": 300000},
          {"id": "f4", "value": 2750000, "claim": "stated total"}]
_checks = [{"description": "components vs stated total", "operation": "sum",
            "operand_fact_ids": ["f1", "f2", "f3"],
            "against_fact_id": "f4",
            "expected_value": 2650000}]  # model's self-serving number — must be ignored
r = backend.verify(_facts, _checks)
check("expected resolves from the cited fact", r[0]["expected"] == 2750000.0,
      f"got {r[0]['expected']}")
check("self-graded pass becomes a caught mismatch", r[0]["passed"] is False)
_checks2 = [{"description": "dangling ref", "operation": "sum",
             "operand_fact_ids": ["f1"], "against_fact_id": "f99"}]
r2 = backend.verify(_facts, _checks2)
check("dangling against_fact_id errors, not crashes",
      r2[0]["error"] is not None and r2[0]["passed"] is False)
check("prompt demands against_fact_id",
      "against_fact_id" in backend.SYSTEM_PROMPT)
with open(os.path.join(os.path.dirname(__file__), "fixtures",
                       "cached_response.json")) as fh:
    _fx2 = json.load(fh)
check("fixture exercises against_fact_id",
      any(c.get("against_fact_id") for c in _fx2.get("checks", [])))


# ── 18. analyze_stream — in-product CI pipeline, written BEFORE the code ───

print("\n=== 18. analyze_stream (CI pipeline) ===")
check("backend exposes analyze_stream", hasattr(backend, "analyze_stream"))
if hasattr(backend, "analyze_stream") and os.path.exists("sample_report.pdf"):
    _keys = {k: os.environ.pop(k, None)
             for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        events = list(backend.analyze_stream(
            "sample_report.pdf", "Does revenue add up?"))
        stages = [e["stage"] for e in events if e["status"] in ("ok", "fail")]
        check("stages run in CI order",
              stages == ["parse", "redact", "extract",
                         "verify-math", "verify-citations", "release"],
              f"got {stages}")
        check("every completed stage is measured",
              all(e.get("elapsed_ms") is not None and e["elapsed_ms"] >= 0
                  for e in events if e["status"] in ("ok", "fail")))
        check("gate: nothing before release carries the answer",
              all(not e.get("result") for e in events[:-1]))
        final = events[-1]
        check("release carries the full result",
              final["stage"] == "release" and isinstance(final.get("result"), dict)
              and final["result"].get("error") is None
              and bool(final["result"].get("answer")))
        ref = backend.analyze("sample_report.pdf", "Does revenue add up?")
        check("analyze() and stream release agree on shape",
              set(ref.keys()) == set(final["result"].keys()))
        # hard failure: pipeline stops, later stages skip, error still released
        bad = list(backend.analyze_stream(None, "q"))
        bstat = {e["stage"]: e["status"] for e in bad}
        check("hard fail stops at parse", bstat.get("parse") == "fail")
        check("later stages are skipped, not run",
              bstat.get("extract") == "skip" and bstat.get("verify-math") == "skip")
        check("failure is still released with an error",
              bad[-1]["stage"] == "release"
              and bad[-1]["result"].get("error") is not None)
    finally:
        for k, v in _keys.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)


# ── 19. FastAPI frontend server — written BEFORE the implementation ────────

print("\n=== 19. FastAPI server ===")
try:
    import server as _srv
    _has_srv = True
except Exception as _e:
    _has_srv = False
    print(f"  (server import failed: {_e})")
check("server module importable", _has_srv)
if _has_srv and os.path.exists("sample_report.pdf"):
    from fastapi.testclient import TestClient
    _client = TestClient(_srv.app)
    _r = _client.get("/")
    check("serves the frontend at /",
          _r.status_code == 200 and "FINVERIFY" in _r.text.upper())
    check("frontend is self-contained (no tailwind CDN)",
          "cdn.tailwindcss.com" not in _r.text)
    check("frontend carries no fabricated tx hashes",
          "8xA9" not in _r.text and "SETTLED" not in _r.text)
    _saved = {k: os.environ.pop(k, None)
              for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        with open("sample_report.pdf", "rb") as fh:
            _r = _client.post(
                "/api/analyze",
                files={"file": ("sample_report.pdf", fh, "application/pdf")},
                data={"question": "Does revenue add up?"},
            )
        check("analyze endpoint streams", _r.status_code == 200)
        _lines = [json.loads(l) for l in _r.text.strip().splitlines()]
        _stages = [e["stage"] for e in _lines if e["status"] in ("ok", "fail")]
        check("NDJSON events in CI order",
              _stages == ["parse", "redact", "extract",
                          "verify-math", "verify-citations", "release"],
              f"got {_stages}")
        check("release event carries the answer",
              _lines[-1]["stage"] == "release"
              and bool(_lines[-1]["result"].get("answer")))
        check("server deleted the upload after analysis",
              not any(fname.startswith("finverify_")
                      for fname in os.listdir(tempfile.gettempdir())))
    finally:
        for k, v in _saved.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)

# ── Summary ───────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL:
    print("  WARNING: fix failures before demo day!")
    sys.exit(1)
else:
    print("  All clear. Ship it.")

