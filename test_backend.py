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


# ── 21. Trends & risks (Lab 1: "trends, patterns, exceptions, risks") ──────
# Written BEFORE the implementation.

print("\n=== 21. Trends & risks ===")
check("prompt demands structured risks", '"risks"' in backend.SYSTEM_PROMPT)
check("prompt demands percent_change trend checks for multi-period docs",
      "percent_change" in backend.SYSTEM_PROMPT
      and "period" in backend.SYSTEM_PROMPT.lower())
with open(os.path.join(os.path.dirname(__file__), "fixtures",
                       "cached_response.json")) as fh:
    _fx3 = json.load(fh)
check("fixture carries at least one evidenced risk",
      any(r.get("evidence_fact_ids") for r in _fx3.get("risks", [])))
check("fixture carries a percent_change trend check",
      any(c.get("operation") == "percent_change" for c in _fx3.get("checks", [])))
# evidence discipline: a risk citing an unknown fact id is excluded outright
_kept = backend._clean_risks(
    [{"description": "real", "severity": "HIGH", "evidence_fact_ids": ["f1"]},
     {"description": "phantom", "severity": "low", "evidence_fact_ids": ["f1", "f99"]},
     {"description": "uncited", "severity": "low", "evidence_fact_ids": []},
     "not a dict"],
    [{"id": "f1"}])
check("evidenced risk kept with normalised severity",
      len(_kept) == 1 and _kept[0]["severity"] == "high")
if os.path.exists("sample_report.pdf"):
    _pages = backend.parse_pdf("sample_report.pdf")
    check("sample now carries a prior period (page 2)",
          len(_pages) >= 2 and "prior quarter" in _pages[1][1].lower())
    _ks = {k: os.environ.pop(k, None) for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        _o = backend.analyze("sample_report.pdf", "How did revenue trend, and what are the risks?")
        check("analyze surfaces evidenced risks", len(_o.get("risks") or []) > 0)
        _fids = {f["id"] for f in _o["facts"]}
        check("every surfaced risk cites known facts",
              all(set(r["evidence_fact_ids"]) <= _fids for r in _o["risks"]))
        _trend = [c for c in _o["checks"] if "growth" in c["description"].lower()
                  or "prior" in c["description"].lower()]
        check("the trend check verifies deterministically",
              any(c.get("passed") for c in _trend), f"trend checks: {_trend}")
    finally:
        for k, v in _ks.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)


# ── 22. bounded retry before fallback (#34) — written BEFORE the code ──────

print("\n=== 22. bounded retry ===")
import inspect as _insp
_calls = {"n": 0}
_orig = backend._call_deepseek
def _flaky(pages, q):
    _calls["n"] += 1
    if _calls["n"] == 1:
        raise RuntimeError("transient network blip")
    return {"answer": "x"}
backend._call_deepseek = _flaky
try:
    _out, _cached = backend.ask_llm([("Page 1", "t")], "q")
    check("one transient failure is retried and succeeds",
          _out == {"answer": "x"} and _cached is False and _calls["n"] == 2,
          f"calls={_calls['n']}")
    _calls["n"] = 0
    def _dead(pages, q):
        _calls["n"] += 1
        raise RuntimeError("hard down")
    backend._call_deepseek = _dead
    _oldfb = os.environ.pop("DEMO_FALLBACK", None)
    try:
        try:
            backend.ask_llm([("Page 1", "t")], "q")
            check("double failure raises", False)
        except RuntimeError as e:
            check("double failure raises after exactly 2 attempts",
                  _calls["n"] == 2, f"calls={_calls['n']}")
            check("error names the retry", "retry" in str(e).lower(), str(e))
        _calls["n"] = 0
        os.environ["DEMO_FALLBACK"] = "1"
        _out, _cached = backend.ask_llm([("Page 1", "t")], "q")
        check("fallback engages only after the retry",
              _cached is True and _calls["n"] == 2, f"calls={_calls['n']}")
    finally:
        os.environ.pop("DEMO_FALLBACK", None)
        if _oldfb is not None:
            os.environ["DEMO_FALLBACK"] = _oldfb
finally:
    backend._call_deepseek = _orig
check("client call carries an explicit timeout",
      "timeout" in _insp.getsource(backend._call_deepseek))


# ── 23. golden XLSX fixture (#33) — written BEFORE the code ────────────────

print("\n=== 23. golden XLSX ===")
import hashlib as _hl
check("make_sample exposes an xlsx generator",
      hasattr(__import__("make_sample"), "build_xlsx"))
if hasattr(__import__("make_sample"), "build_xlsx"):
    import make_sample as _ms
    _xp = os.path.join(tempfile.gettempdir(), "_golden_check.xlsx")
    _ms.build_xlsx(_xp)
    _sheets = backend.parse_xlsx(_xp)
    _text = "\n".join(t for _, t in _sheets)
    _sha = _hl.sha256(_text.encode()).hexdigest()
    with open(os.path.join(os.path.dirname(__file__), "fixtures",
                           "golden_xlsx.json")) as fh:
        _g = json.load(fh)
    check("parsed text matches the golden sha", _sha == _g["text_sha256"],
          f"got {_sha[:16]}")
    _clean, _n = backend.redact(_text)
    check("golden redaction count matches", _n == _g["redaction_count"],
          f"got {_n}")
    check("xlsx carries the same planted discrepancy",
          "2,750,000" in _text or "2750000" in _text)
    os.remove(_xp)


# ── 24. Gradio app risks parity (#32) — written BEFORE the code ────────────

print("\n=== 24. Gradio risks parity ===")
try:
    import app as _app
    check("app.py imports (CI now guards the Gradio UI)", True)
except Exception as _e:
    check("app.py imports (CI now guards the Gradio UI)", False, str(_e))
    _app = None
if _app is not None:
    check("app exposes render_risks", hasattr(_app, "render_risks"))
    if hasattr(_app, "render_risks"):
        _html = _app.render_risks({"risks": [
            {"description": "Total does not reconcile", "severity": "high",
             "evidence_fact_ids": ["f1", "f4"]}]})
        check("risk description and evidence rendered",
              "Total does not reconcile" in _html and "f1" in _html)
        check("severity is visible as text, not color alone",
              "high" in _html.lower())
        check("no risks -> empty string, no placeholder card",
              _app.render_risks({"risks": []}) == "")
    check("FAKE payload carries risks for UI development",
          bool(_app.FAKE.get("risks")))

# ── 20. chain layer — verification-gated payments, written BEFORE the code ─

print("\n=== 20. chain (Solana devnet, offline fake transport) ===")
try:
    import chain as _ch
    _has_ch = True
except Exception as _e:
    _has_ch = False
    print(f"  (chain import failed: {_e})")
check("chain module importable", _has_ch)
if _has_ch:
    from solders.keypair import Keypair as _KP
    from solders.transaction import Transaction as _TX
    import base64 as _b64

    check("memo program id is the verified v2 address",
          str(_ch.MEMO_PROGRAM_ID) == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")

    _d1 = _ch.result_digest({"a": 1, "b": [2, 3]})
    _d2 = _ch.result_digest({"b": [2, 3], "a": 1})
    check("digest is key-order independent", _d1 == _d2 and len(_d1) == 64)

    _calls = []
    def _fake(payload):
        _calls.append(payload)
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "id": 1, "result":
                    {"value": {"blockhash": "1" * 32, "lastValidBlockHeight": 1}}}
        if payload["method"] == "sendTransaction":
            return {"jsonrpc": "2.0", "id": 1, "result": "FAKESIG" + "1" * 60}
        return {"jsonrpc": "2.0", "id": 1, "result": None}

    _kp = _KP()
    _res = {"answer": "x", "checks": [{"passed": True, "error": None}],
            "facts": [{"verified_in_source": True}],
            "fallback_used": False, "error": None}
    _n = _ch.notarize(_res, keypair=_kp, transport=_fake)
    check("notarize returns signature + digest + explorer link",
          _n["signature"].startswith("FAKESIG")
          and _n["digest"] == _ch.result_digest(_res)
          and "explorer.solana.com" in _n["explorer"])
    _sent = next(c for c in _calls if c["method"] == "sendTransaction")
    _tx = _TX.from_bytes(_b64.b64decode(_sent["params"][0]))
    check("memo instruction carries the digest on-chain",
          _ch.result_digest(_res).encode() in bytes(_tx.message.instructions[0].data))

    def _blocked(res, needle):
        try:
            _ch.pay_if_verified(res, str(_KP().pubkey()), 1000,
                                keypair=_kp, transport=_fake)
            return False
        except _ch.PaymentBlocked as e:
            return needle in str(e)
    check("refuses when a check failed",
          _blocked({**_res, "checks": [{"passed": False, "error": None}]}, "refused"))
    check("refuses when no checks ran",
          _blocked({**_res, "checks": []}, "never paid"))
    check("refuses unpinned facts",
          _blocked({**_res, "facts": [{"verified_in_source": False}]}, "not pinned"))
    check("refuses cached fallback results",
          _blocked({**_res, "fallback_used": True}, "cached"))
    check("refuses analysis errors",
          _blocked({**_res, "error": "boom"}, "error"))

    _calls.clear()
    _pay = _ch.pay_if_verified(_res, str(_KP().pubkey()), 1000,
                               keypair=_kp, transport=_fake)
    check("pays a fully verified result", _pay["signature"].startswith("FAKESIG"))
    _sent = next(c for c in _calls if c["method"] == "sendTransaction")
    _tx = _TX.from_bytes(_b64.b64decode(_sent["params"][0]))
    check("payment tx carries transfer + memo (2 instructions)",
          len(_tx.message.instructions) == 2)

    _old = os.environ.pop("SOLANA_SECRET_KEY", None)
    try:
        _ch.load_keypair()
        check("missing SOLANA_SECRET_KEY raises", False)
    except Exception as e:
        check("missing SOLANA_SECRET_KEY raises", "SOLANA_SECRET_KEY" in str(e))
    finally:
        if _old is not None:
            os.environ["SOLANA_SECRET_KEY"] = _old




# ── 22. contribution card — real address, real QR, no fabrications ─────────
# Written BEFORE the implementation.

print("\n=== 22. contribution card ===")
try:
    import server as _sv2
    from fastapi.testclient import TestClient as _TC2
    _c2 = _TC2(_sv2.app)
    _old_addr = os.environ.pop("VERIPAY_WALLET_ADDRESS", None)
    _old_url = os.environ.pop("VERIPAY_WALLET_URL", None)
    try:
        _r = _c2.get("/api/contribution")
        check("no address configured -> 204, card stays hidden",
              _r.status_code == 204)
        os.environ["VERIPAY_WALLET_ADDRESS"] = "TestAddr123abc"
        _r = _c2.get("/api/contribution")
        _j = _r.json()
        check("configured address is returned",
              _r.status_code == 200 and _j["address"] == "TestAddr123abc")
        check("QR is generated locally as a data URI",
              _j["qr"].startswith("data:image/svg"))
        _page = _c2.get("/").text
        check("frontend carries the contribution card markup",
              "CONTRIBUTION_PROTOCOL" in _page and "COPY_ADDRESS" in _page)
        check("no hotlinked placeholder QR, no placeholder address",
              "googleusercontent" not in _page and "HATA...Solana" not in _page)
    finally:
        os.environ.pop("VERIPAY_WALLET_ADDRESS", None)
        if _old_addr is not None:
            os.environ["VERIPAY_WALLET_ADDRESS"] = _old_addr
        if _old_url is not None:
            os.environ["VERIPAY_WALLET_URL"] = _old_url
except Exception as _e:
    print(f"  FAIL contribution spec crashed: {_e}")
    FAIL += 1


# ── 23. clean_invoice.pdf — a real document for the paid leg ───────────────
# Written BEFORE the implementation. The live devnet payment leg must pay
# against a genuine document that passes verification, not a synthetic
# hand-built result dict. This spec checks the generated PDF's numbers
# genuinely sum (parsed straight from the text, never hardcoded), that its
# PII is present pre-redaction and gone post-redaction, and that
# devnet_live.py has actually been switched over to it.

print("\n=== 23. clean_invoice.pdf ===")
import re as _re23

try:
    import make_clean_invoice as _mci

    _mci.build_pdf("clean_invoice.pdf")
    check("generator writes clean_invoice.pdf",
          os.path.exists("clean_invoice.pdf"))

    _pages23 = backend.parse_pdf("clean_invoice.pdf")
    _full23 = "\n".join(text for _, text in _pages23)

    # Parse the line items genuinely — no hardcoded expected sum.
    _items23 = [
        float(m.group(2).replace(",", ""))
        for m in _re23.finditer(
            r"^(?!Total)([A-Za-z][A-Za-z ]+?)\s+([\d,]+)\s*$", _full23,
            _re23.MULTILINE,
        )
    ]
    check("found line items", len(_items23) >= 3, f"got {_items23}")

    _total_m23 = _re23.search(r"Total amount due\s+([\d,]+)\s*$", _full23,
                              _re23.MULTILINE)
    check("found stated total", _total_m23 is not None)
    _total23 = float(_total_m23.group(1).replace(",", ""))
    check("line items genuinely sum to the stated total",
          abs(sum(_items23) - _total23) < 0.01,
          f"{_items23} -> {sum(_items23)} vs {_total23}")

    _prior_m23 = _re23.search(r"Total amount due prior quarter\s+([\d,]+)",
                              _full23)
    _growth_m23 = _re23.search(r"Growth vs prior quarter:\s+([\d.]+)%",
                               _full23)
    check("found prior-period total", _prior_m23 is not None)
    check("found stated growth rate", _growth_m23 is not None)
    if _prior_m23 and _growth_m23:
        _prior23 = float(_prior_m23.group(1).replace(",", ""))
        _growth23 = float(_growth_m23.group(1))
        _computed_growth23 = (_total23 - _prior23) / _prior23 * 100
        check("stated growth is exactly consistent with the figures",
              abs(_computed_growth23 - _growth23) < 0.01,
              f"computed {_computed_growth23} vs stated {_growth23}")

    # PII present pre-redaction.
    _page1_23 = _pages23[0][1]
    check("name present pre-redaction", "Siti Binti Hassan" in _page1_23)
    check("email present pre-redaction",
          "siti.hassan@meridian.com.my" in _page1_23)
    check("phone present pre-redaction", "019-8765432" in _page1_23)
    check("IC present pre-redaction", "900101-14-5678" in _page1_23)

    # PII gone post-redaction.
    _clean23, _n23 = backend.redact(_page1_23)
    check("redaction did real work", _n23 > 0)
    check("name gone post-redaction", "Siti Binti Hassan" not in _clean23)
    check("email gone post-redaction",
          "siti.hassan@meridian.com.my" not in _clean23)
    check("phone gone post-redaction", "019-8765432" not in _clean23)
    check("IC gone post-redaction", "900101-14-5678" not in _clean23)

    _devnet_src23 = open(
        os.path.join(os.path.dirname(__file__), "scripts", "devnet_live.py")
    ).read()
    check("synthetic result dict marker is gone",
          '"answer": "verified"' not in _devnet_src23)
    check("devnet_live.py references clean_invoice.pdf",
          "clean_invoice.pdf" in _devnet_src23)
except Exception as _e23:
    print(f"  FAIL clean_invoice spec crashed: {_e23}")
    FAIL += 1



# ── 25. Hata read-only client (deposit address) — written BEFORE the code ──

print("\n=== 25. Hata read-only client ===")
try:
    import hata as _ht
    _has_ht = True
except Exception as _e:
    _has_ht = False
    print(f"  (hata import failed: {_e})")
check("hata module importable", _has_ht)
if _has_ht:
    import hashlib as _hhl, hmac as _hm, json as _js
    # signature: HMAC-SHA256 over the compact JSON of the sorted params.
    # (Confirmed against the live API 2026-08-22: the raw k=v&k=v scheme
    # was rejected with "invalid hash" on every base/method tried; the
    # compact-JSON-of-sorted-body canonicalization is what the exchange
    # actually verifies.)
    _params = {"token_symbol": "SOL", "network_name": "Solana",
               "timestamp": 1730000000}
    _qs, _sig = _ht.sign(_params, "s3cret")
    check("canonical string is compact sorted-key JSON",
          _qs == _js.dumps(_params, sort_keys=True, separators=(",", ":")), _qs)
    check("signature is hmac-sha256 of the canonical string",
          _sig == _hm.new(b"s3cret", _qs.encode(), _hhl.sha256).hexdigest())
    # read-only enforcement: only allowlisted retrieval paths may be called
    check("withdrawal endpoints are not even mentioned in the module",
          "withdrawal" not in open(os.path.join(
              os.path.dirname(__file__), "hata.py")).read())
    try:
        _ht.request("/wallet/sapi/withdrawal/create", {}, transport=lambda **k: {})
        check("non-allowlisted path refused", False)
    except _ht.ReadOnlyViolation:
        check("non-allowlisted path refused", True)
    # transport capture: headers + body
    _seen = {}
    def _cap(url, headers, body):
        _seen.update(url=url, headers=headers, body=body)
        # Real shape confirmed live 2026-08-22: payload is nested under
        # "data", sibling to "is_exist"/"status" — not top-level fields.
        return {"data": {"DepositAddress": "So1anaAddr", "Network": "Solana",
                          "Symbol": "SOL", "Tag": ""},
                "is_exist": True, "status": "success"}
    _old = {k: os.environ.pop(k, None) for k in ("HATA_API_KEY", "HATA_API_SECRET")}
    os.environ["HATA_API_KEY"] = "kid"
    os.environ["HATA_API_SECRET"] = "sek"
    try:
        _out = _ht.get_deposit_address("SOL", "Solana", transport=_cap)
        check("deposit address returned", _out["address"] == "So1anaAddr")
        check("X-API-KEY header carried", _seen["headers"].get("X-API-KEY") == "kid")
        check("Signature header carried", len(_seen["headers"].get("Signature", "")) == 64)
        check("timestamp included in body", "timestamp" in _seen["body"])
        os.environ.pop("HATA_API_KEY")
        try:
            _ht.get_deposit_address("SOL", "Solana", transport=_cap)
            check("missing key raises", False)
        except Exception as e:
            check("missing key raises naming HATA_API_KEY", "HATA_API_KEY" in str(e))
    finally:
        for k, v in _old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    # server integration: hata feeds the contribution card when env address unset
    import server as _sv3
    from fastapi.testclient import TestClient as _TC3
    _c3 = _TC3(_sv3.app)
    _oa = os.environ.pop("VERIPAY_WALLET_ADDRESS", None)
    _sv3._hata_cached = None
    _oldfn = _ht.get_deposit_address
    _ht.get_deposit_address = lambda *a, **k: {"address": "HataLive123", "network": "Solana"}
    os.environ["HATA_API_KEY"] = "kid"; os.environ["HATA_API_SECRET"] = "sek"
    try:
        _r = _c3.get("/api/contribution")
        check("hata feeds the card when env address unset",
              _r.status_code == 200 and _r.json()["address"] == "HataLive123"
              and _r.json().get("source") == "hata")
        os.environ["VERIPAY_WALLET_ADDRESS"] = "EnvWins456"
        _r = _c3.get("/api/contribution")
        check("explicit env address always wins",
              _r.json()["address"] == "EnvWins456" and _r.json().get("source") == "env")
    finally:
        _ht.get_deposit_address = _oldfn
        os.environ.pop("VERIPAY_WALLET_ADDRESS", None)
        os.environ.pop("HATA_API_KEY", None); os.environ.pop("HATA_API_SECRET", None)
        if _oa is not None:
            os.environ["VERIPAY_WALLET_ADDRESS"] = _oa

# ── Summary ───────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL:
    print("  WARNING: fix failures before demo day!")
    sys.exit(1)
else:
    print("  All clear. Ship it.")

