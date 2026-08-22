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
                         "verify-math", "verify-citations",
                         "analyse-patterns", "release"],
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
          _r.status_code == 200 and "VERIPAY" in _r.text.upper())
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
                          "verify-math", "verify-citations",
                          "analyse-patterns", "release"],
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


# ── 22. Verified insights summary (Lab 1: "concise summaries") ─────────────
# The insights summary must be COMPOSED from verified data, never carry a
# number the pipeline did not re-check. Written to lock that guarantee.

print("\n=== 22. Verified insights summary ===")

# The _empty_result() bug: an error/empty result must carry NO risks and an
# empty insights string — never a placeholder.
_er = backend._empty_result()
check("empty result carries no phantom risks", _er["risks"] == [])
check("empty result has an insights key", "insights" in _er)
check("empty result insights is blank", _er["insights"] == "")
check("empty result has no leftover placeholder ids",
      "f1" not in json.dumps(_er))

# build_insights composes from verified rows only. Note the descriptions
# below deliberately CONTAIN unverified numbers (9,999,999 / 42% / 7,777) —
# the masking guarantee is that none of those reach the output.
_ins_facts = [{"id": "f1", "value": 1200000}, {"id": "f2", "value": 1150000},
              {"id": "f4", "value": 2750000}]
_ins_checks = [
    {"description": "Stated total vs sum of segments (model claims 9,999,999)",
     "operation": "sum", "expected": 2750000.0,
     "actual": 2650000.0, "passed": False, "error": None},
    {"description": "Revenue growth of a claimed 42% vs prior quarter",
     "operation": "percent_change", "expected": 10.0,
     "actual": 10.0, "passed": True, "error": None},
    {"description": "Unresolvable ratio", "operation": "percent_change",
     "expected": None, "actual": None,
     "passed": False, "error": "cannot compute percent change from zero"},
]
_ins_risks = [
    {"description": "headline off by RM 7,777 does not reconcile",
     "severity": "high", "evidence_fact_ids": ["f1", "f4"]},
    {"description": "trend inherits the gap", "severity": "medium",
     "evidence_fact_ids": ["f2"]},
]
_ins_summary = {"facts_extracted": 3, "checks_run": 3,
                "checks_passed": 1, "checks_failed": 1}
_ins = backend.build_insights(_ins_facts, _ins_checks, _ins_risks, _ins_summary)
check("insights is a non-empty string", isinstance(_ins, str) and bool(_ins))
check("insights surfaces the verified trend", "Trend verified" in _ins)
check("insights surfaces the confirmed exception", "Exception" in _ins)
check("insights states the size of the gap", "100,000" in _ins)
check("insights lists evidenced risks", "Risk (high)" in _ins
      and "Risk (medium)" in _ins)
check("high-severity risk is ordered before medium",
      _ins.index("Risk (high)") < _ins.index("Risk (medium)"))
check("insights reports coverage", "Coverage:" in _ins
      and "re-checked in Python" in _ins)
# Anti-hallucination (the real test): numbers the model wrote INTO its
# descriptions must be masked, never echoed. These were fed above.
check("model's unverified number in a check description is masked",
      "9,999,999" not in _ins and "9999999" not in _ins)
check("model's unverified percentage in a description is masked",
      "42%" not in _ins)
check("model's unverified number in a risk description is masked",
      "7,777" not in _ins and "7777" not in _ins)
check("masking leaves a placeholder", "#" in _ins)
# But the Python-COMPUTED numbers must still be present and correct.
check("computed figures survive masking",
      "2,750,000" in _ins and "2,650,000" in _ins and "10%" in _ins)
# Trend classification is structural (operation), not prose-driven: a sum
# check whose description says "change" must NOT be labelled a trend.
_kw = backend.build_insights(
    [], [{"description": "net change in cash", "operation": "sum",
          "expected": 5.0, "actual": 5.0, "passed": True, "error": None}],
    [], {"facts_extracted": 0, "checks_run": 1})
check("a passed sum is not mislabelled a trend", "Trend verified" not in _kw)
# A failed check missing a figure degrades to just its description.
_partial = backend.build_insights(
    [], [{"description": "orphan", "operation": "sum", "expected": None,
          "actual": None, "passed": False, "error": None}],
    [], {"facts_extracted": 0, "checks_run": 1})
check("partial failed check omits n/a numeric prose", "n/a" not in _partial)

# Nothing to verify -> empty summary, not fabricated prose.
check("no checks yields empty insights",
      backend.build_insights([], [], [], {"checks_run": 0}) == "")

# End-to-end: analyze() surfaces the insights via the release event.
if os.path.exists("sample_report.pdf"):
    _ks2 = {k: os.environ.pop(k, None)
            for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        _o2 = backend.analyze("sample_report.pdf",
                              "Summarise the findings and any risks.")
        check("analyze surfaces an insights summary",
              bool(str(_o2.get("insights", "")).strip()))
        check("insights mentions the discrepancy the checks found",
              "Exception" in _o2["insights"] or "mismatch" in _o2["insights"])
    finally:
        for k, v in _ks2.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)


# ── 23. Pattern analysis (Lab 1: "patterns") — after verify+citations ──────
# Patterns are proposed by the model but RECOMPUTED in Python from cited
# facts, dropped if uncited, and their prose is digit-masked. Written to
# lock all of that.

print("\n=== 23. Pattern analysis ===")
check("prompt demands structured patterns", '"patterns"' in backend.SYSTEM_PROMPT)
check("prompt names the three pattern kinds",
      "composition" in backend.SYSTEM_PROMPT and "ratio" in backend.SYSTEM_PROMPT
      and "sign" in backend.SYSTEM_PROMPT)
check("empty result seeds patterns as []", backend._empty_result()["patterns"] == [])
check("analyse-patterns is in the pipeline stages after citations",
      backend.PIPELINE_STAGES.index("analyse-patterns")
      > backend.PIPELINE_STAGES.index("verify-citations")
      and backend.PIPELINE_STAGES.index("analyse-patterns")
      < backend.PIPELINE_STAGES.index("release"))

_pf = [{"id": "f1", "value": 1200000}, {"id": "f4", "value": 2750000},
       {"id": "f5", "value": 1450000}, {"id": "f6", "value": -50000}]
_pats = [
    # composition: 1,200,000 / 2,750,000 * 100 = 43.64
    {"kind": "composition", "description": "product share of revenue",
     "operand_fact_ids": ["f1"], "against_fact_id": "f4",
     "evidence_fact_ids": ["f1", "f4"]},
    # ratio: 1,450,000 / 2,750,000 * 100 = 52.73
    {"kind": "ratio", "description": "opex to revenue at a claimed 999%",
     "operand_fact_ids": ["f5", "f4"], "evidence_fact_ids": ["f5", "f4"]},
    # sign: -50,000 is negative -> the flagged condition holds
    {"kind": "sign", "description": "net line is negative",
     "operand_fact_ids": ["f6"], "evidence_fact_ids": ["f6"]},
    # phantom evidence -> dropped
    {"kind": "composition", "description": "phantom",
     "operand_fact_ids": ["f1"], "against_fact_id": "f4",
     "evidence_fact_ids": ["f1", "f99"]},
    # unknown kind -> dropped
    {"kind": "outlier", "description": "not supported",
     "operand_fact_ids": ["f1"], "evidence_fact_ids": ["f1"]},
    "not a dict",
]
_pr = backend.verify_patterns(_pf, _pats)
check("only evidenced, known-kind patterns survive", len(_pr) == 3,
      f"got {len(_pr)}")
_by_kind = {p["kind"]: p for p in _pr}
check("composition share recomputed in Python",
      abs(_by_kind["composition"]["actual"] - 43.64) < 0.01)
check("composition is confirmed", _by_kind["composition"]["passed"] is True)
check("ratio recomputed in Python",
      abs(_by_kind["ratio"]["actual"] - 52.73) < 0.01)
check("sign pattern confirmed on a negative value",
      _by_kind["sign"]["passed"] is True and _by_kind["sign"]["actual"] == -50000.0)
# The model's self-serving expected value must never turn a pattern into a
# free pass — patterns recompute, they do not trust typed numbers.
_cheat = backend.verify_patterns(
    [{"id": "f1", "value": 100}, {"id": "f2", "value": 400}],
    [{"kind": "ratio", "description": "d", "operand_fact_ids": ["f1", "f2"],
      "expected_value": 999, "evidence_fact_ids": ["f1", "f2"]}])
check("a model-typed expected_value cannot fake a pattern pass",
      _cheat[0]["passed"] is False and abs(_cheat[0]["actual"] - 25.0) < 0.01)
# Unverifiable: a sign pattern whose fact is unusable comes back as error,
# never as an established finding.
_unver = backend.verify_patterns(
    [{"id": "f1", "value": "not a number"}],
    [{"kind": "sign", "description": "x", "operand_fact_ids": ["f1"],
      "evidence_fact_ids": ["f1"]}])
check("unusable pattern fact -> unverifiable, not passed",
      _unver and _unver[0]["error"] is not None and _unver[0]["passed"] is False)

# Digit-masking in the insights patterns section: the model's "999%" in a
# pattern description must not survive; the Python-computed 52.73% must.
_pins = backend.build_insights(_pf, [], [],
                               {"facts_extracted": 4, "checks_run": 0}, _pr)
check("pattern insights show a Python-computed figure", "52.73%" in _pins)
check("model's number inside a pattern description is masked",
      "999%" not in _pins)
check("pattern insights carry a mask placeholder", "#" in _pins)

# End-to-end via the fallback fixture: patterns surface in the release.
if os.path.exists("sample_report.pdf"):
    _ks3 = {k: os.environ.pop(k, None)
            for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
    os.environ["DEMO_FALLBACK"] = "1"
    try:
        _po = backend.analyze("sample_report.pdf",
                              "What patterns do you see?")
        check("analyze surfaces verified patterns",
              len(_po.get("patterns") or []) > 0)
        _pfids = {f["id"] for f in _po["facts"]}
        check("every surfaced pattern cites known facts",
              all(set(p["evidence_fact_ids"]) <= _pfids
                  for p in _po["patterns"]))
        check("insights include a pattern line",
              "Pattern verified" in _po["insights"]
              or "Pattern (unverifiable)" in _po["insights"])
    finally:
        for k, v in _ks3.items():
            if v is not None:
                os.environ[k] = v
        os.environ.pop("DEMO_FALLBACK", None)


# ── 24. Improvement batch: negatives, fences, anti-cheat visibility, ───────
#     suppressed counts, opex ratio, flag wording, UTF-8. Written to lock
#     the senior-approved improvements A/C/B/H/I/F.

print("\n=== 24. Improvement batch ===")

# A — accounting-parentheses negatives.
check("bracketed number is negative", backend._to_number("(50,000)") == -50000.0)
check("bracketed decimal is negative",
      abs(backend._to_number("(1,234.50)") - (-1234.5)) < 0.001)
check("plain number stays positive", backend._to_number("1,200,000") == 1200000.0)
check("currency-prefixed stays positive", backend._to_number("RM 2,750,000") == 2750000.0)
check("a bracketed negative flips a difference",
      backend.verify(
          [{"id": "f1", "value": 100000}, {"id": "f2", "value": "(30,000)"}],
          [{"description": "d", "operation": "sum",
            "operand_fact_ids": ["f1", "f2"], "expected_value": 70000}]
      )[0]["actual"] == 70000.0)

# C — case-insensitive fence strip.
check("uppercase JSON fence is stripped",
      backend._extract_json('```JSON\n{"answer":"x"}\n```').get("answer") == "x")

# H — anti-cheat visibility. The model's typed number is preserved for
# display, the override is flagged, but expected/passed are UNCHANGED.
_hf = [{"id": "f1", "value": 1200000}, {"id": "f2", "value": 1150000},
       {"id": "f3", "value": 300000}, {"id": "f4", "value": 2750000}]
_hc = [{"description": "components vs stated", "operation": "sum",
        "operand_fact_ids": ["f1", "f2", "f3"], "against_fact_id": "f4",
        "expected_value": 2650000}]  # model's self-serving number
_hr = backend.verify(_hf, _hc)[0]
check("model_stated preserves the model's typed number",
      _hr["model_stated"] == 2650000.0)
check("overridden flags the anti-cheat substitution", _hr["overridden"] is True)
check("expected still comes from the cited fact (invariant)",
      _hr["expected"] == 2750000.0)
check("passed logic unchanged by the display field", _hr["passed"] is False)
# No override flag when the model's number agrees with the cited fact.
_hr2 = backend.verify(
    [{"id": "f1", "value": 100}, {"id": "f2", "value": 200}, {"id": "f3", "value": 300}],
    [{"description": "d", "operation": "sum", "operand_fact_ids": ["f1", "f2"],
      "against_fact_id": "f3", "expected_value": 300}])[0]
check("no override when model agrees with the cited figure",
      _hr2["overridden"] is False)

# I — suppressed-claim counts surface in the stage detail, not the summary.
check("summary dict has no suppressed keys (invariant)",
      set(backend._empty_result()["summary"].keys())
      == {"facts_extracted", "checks_run", "checks_passed", "checks_failed"})

# F — the fixture now exercises an opex-to-revenue ratio pattern.
with open(os.path.join(os.path.dirname(__file__), "fixtures",
                       "cached_response.json"), encoding="utf-8") as fh:
    _fx4 = json.load(fh)
check("prompt demands opex-to-revenue ratio",
      "opex-to-revenue" in backend.SYSTEM_PROMPT.lower()
      or "opex" in backend.SYSTEM_PROMPT.lower())
check("fixture carries a ratio pattern",
      any(p.get("kind") == "ratio" for p in _fx4.get("patterns", [])))

# B — a confirmed sign/threshold pattern reads as a FLAG, not a reassuring
# "verified". (Presentation wording; verify() passed logic is unchanged.)
_flag = backend.build_insights(
    [{"id": "f1", "value": -50000}], [], [],
    {"facts_extracted": 1, "checks_run": 0},
    [{"kind": "sign", "description": "net loss", "actual": -50000.0,
      "passed": True, "error": None, "evidence_fact_ids": ["f1"]}])
check("sign pattern insight is framed as a flag", "Flag confirmed" in _flag)
check("sign pattern insight is not reassuring 'verified'",
      "Pattern verified" not in _flag)

# UTF-8 regression: the fixture loads without mojibake (the em-dash bug).
_rec = str(_fx4.get("recommendation", ""))
check("fixture recommendation loads without mojibake",
      "â€" not in _rec and "—" in _fx4.get("answer", ""))


# ── 25. bounded retry before fallback (#34) — written BEFORE the code ──────

print("\n=== 25. bounded retry ===")
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


# ── 26. golden XLSX fixture (#33) — written BEFORE the code ────────────────

print("\n=== 26. golden XLSX ===")
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


# ── 27. Gradio app risks parity (#32) — written BEFORE the code ────────────

print("\n=== 27. Gradio risks parity ===")
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
        # In the current layout risks have their own panel, so an empty
        # risk set renders a positive "no risks flagged" confirmation
        # rather than nothing — it must not render a phantom risk card.
        _empty_html = _app.render_risks({"risks": []})
        check("no risks -> no phantom risk card",
              "risk-card" not in _empty_html and "RISK" not in _empty_html.upper())
    check("FAKE payload carries risks for UI development",
          bool(_app.FAKE.get("risks")))


# ── 28. tamper-evident audit chain — spec BEFORE code ──────────────────────

print("\n=== 28. audit chain ===")
import hashlib as _ah, hmac as _am
_ks = {k: os.environ.pop(k, None) for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
os.environ["DEMO_FALLBACK"] = "1"
os.environ["AUDIT_HMAC_KEY"] = "test-audit-key"
try:
    _evs = list(backend.analyze_stream("sample_report.pdf", "Does it add up?"))
    check("every event carries prev_hash, row_hash, sig",
          all(e.get("row_hash") and e.get("sig") and "prev_hash" in e
              for e in _evs))
    check("chain verifies end to end", backend.verify_audit_chain(_evs) is True)
    check("sig is HMAC(key, row_hash)",
          _evs[0]["sig"] == _am.new(b"test-audit-key",
                                    _evs[0]["row_hash"].encode(),
                                    _ah.sha256).hexdigest())
    import copy as _cp
    _t = _cp.deepcopy(_evs)
    _t[2]["detail"] = "44 PII item(s) masked"       # rewrite history
    check("mutating any historical event breaks verification",
          backend.verify_audit_chain(_t) is False)
    _t2 = _cp.deepcopy(_evs)
    _t2[1], _t2[2] = _t2[2], _t2[1]                  # reorder
    check("reordering events breaks verification",
          backend.verify_audit_chain(_t2) is False)
    _rel = _evs[-1]
    check("release exposes the chain root",
          _rel["stage"] == "release"
          and _rel["result"]["audit_log_root"] == _rel["row_hash"])
    check("no compliance string-labels introduced",
          "compliance" not in open(os.path.join(
              os.path.dirname(__file__), "backend.py")).read().lower())
finally:
    os.environ.pop("DEMO_FALLBACK", None)
    os.environ.pop("AUDIT_HMAC_KEY", None)
    for k, v in _ks.items():
        if v is not None:
            os.environ[k] = v


# ── 29. renderer registry with gates — spec BEFORE code ────────────────────

print("\n=== 29. renderer registry ===")
_html29 = open(os.path.join(os.path.dirname(__file__), "web",
                            "index.html")).read()
check("a RENDERERS registry object exists", "const RENDERERS" in _html29)
check("every registered type declares a gate", "gate:" in _html29
      and _html29.count("gate:") >= 4)
check("gate failure degrades to a table, never a broken visual",
      "renderFallbackTable" in _html29)
check("unregistered type fails loudly", "Unregistered artifact type"
      in _html29)


# ── 30. AI-chosen charts, verified data only — spec BEFORE code ────────────

print("\n=== 30. AI-chosen charts ===")
check("prompt invites any chart kind", '"charts"' in backend.SYSTEM_PROMPT
      and "any kind" in backend.SYSTEM_PROMPT.lower())
_facts30 = [{"id": "f1", "claim": "Product", "value": 100.0,
             "verified_in_source": True},
            {"id": "f2", "claim": "Services", "value": 50.0,
             "verified_in_source": True},
            {"id": "f3", "claim": "Unpinned", "value": 7.0,
             "verified_in_source": False}]
_charts30 = [
    {"kind": "donut", "title": "Mix",
     "points": [{"fact_id": "f1"}, {"fact_id": "f2"}]},
    {"kind": "hologram", "title": "Exotic",
     "points": [{"fact_id": "f1"}]},
    {"kind": "bar", "title": "Phantom",
     "points": [{"fact_id": "f99"}]},
    {"kind": "bar", "title": "Unpinned",
     "points": [{"fact_id": "f3"}]},
    "not a dict",
]
_cc = backend._clean_charts(_charts30, _facts30)
check("resolved charts keep values from verified facts",
      any(c["title"] == "Mix" and c["points"][0]["value"] == 100.0
          and c["points"][0]["label"] == "Product" for c in _cc))
check("exotic kinds pass through (the UI gate decides rendering)",
      any(c["kind"] == "hologram" for c in _cc))
check("phantom fact refs are discarded",
      not any(c["title"] == "Phantom" for c in _cc))
check("unpinned facts never chart",
      not any(c["title"] == "Unpinned" for c in _cc))
with open(os.path.join(os.path.dirname(__file__), "fixtures",
                       "cached_response.json")) as fh:
    _fx30 = json.load(fh)
check("fixture carries a chart", bool(_fx30.get("charts")))
_ks30 = {k: os.environ.pop(k, None) for k in ("DEEPSEEK_API_KEY", "deepseek_api")}
os.environ["DEMO_FALLBACK"] = "1"
try:
    _o30 = backend.analyze("sample_report.pdf", "Visualize the revenue mix")
    check("analyze surfaces charts with resolved points",
          _o30.get("charts") and all(
              "value" in p for c in _o30["charts"] for p in c["points"]))
finally:
    os.environ.pop("DEMO_FALLBACK", None)
    for k, v in _ks30.items():
        if v is not None:
            os.environ[k] = v
_html30 = open(os.path.join(os.path.dirname(__file__), "web",
                            "index.html")).read()
check("web has a deterministic chart-kind registry",
      "const CHART_KINDS" in _html30)
check("charts render as inline SVG, no library",
      "<svg" in _html30 and "chart.js" not in _html30.lower())
check("unknown kinds degrade through the fallback, stated plainly",
      "not in the deterministic chart registry" in _html30)


# ── 31. console retheme — spec BEFORE code ─────────────────────────────────

print("\n=== 31. console retheme ===")
_h31 = open(os.path.join(os.path.dirname(__file__), "web",
                         "index.html")).read()
check("new console tokens applied",
      all(t in _h31.lower() for t in ("#08090a", "#3ddc97", "#a855f7", "#e4572e")))
check("old background token fully retired", "#131313" not in _h31)
check("three-column grid with the telemetry aside",
      "grid-template-columns" in _h31 and "336px" in _h31)
check("telemetry lives in its own aside", "PIPELINE TELEMETRY" in _h31.upper())
check("no LegoParse contamination, no dead preconnects",
      "legoparse" not in _h31.lower() and "fraunces" not in _h31.lower())
check("registry and gates untouched",
      "const RENDERERS" in _h31 and "renderFallbackTable" in _h31
      and "not in the deterministic chart registry" in _h31)

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
    # This spec is about the no-config case; isolate it from any real
    # HATA_API_KEY/SECRET a developer's .env may have loaded (via
    # backend.load_dotenv()), otherwise it silently exercises the live
    # Hata path instead of the 204-hidden path under test.
    _old_hkey = os.environ.pop("HATA_API_KEY", None)
    _old_hsec = os.environ.pop("HATA_API_SECRET", None)
    _sv2._hata_cached = None
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
        if _old_hkey is not None:
            os.environ["HATA_API_KEY"] = _old_hkey
        if _old_hsec is not None:
            os.environ["HATA_API_SECRET"] = _old_hsec
        _sv2._hata_cached = None
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




# ── 26. wallet audit (paste-any-address, read-only) — spec BEFORE code ─────

print("\n=== 26. wallet audit ===")
try:
    import explorer as _ex
    _has_ex = True
except Exception as _e:
    _has_ex = False
    print(f"  (explorer import failed: {_e})")
check("explorer module importable", _has_ex)
if _has_ex:
    _ADDR = "6BCbkts1TJdvvipwzsebJVfFwuhB6NU4KDPMZQzrjAtz"
    try:
        _ex.fetch_activity("not-a-pubkey!!", transport=lambda m, p: {})
        check("invalid address raises ValueError", False)
    except ValueError:
        check("invalid address raises ValueError", True)
    _ncalls = {"n": 0}
    def _rpc_fake(method, params):
        _ncalls["n"] += 1
        if method == "getSignaturesForAddress":
            return [{"signature": "SIG1", "blockTime": 1730000000, "err": None},
                    {"signature": "SIG2", "blockTime": 1730000100, "err": None}]
        if method == "getTransaction":
            sig = params[0]
            memo = ('Program log: Memo (len 84): '
                    '"veripay:paid:sha256:abcd"') if sig == "SIG1" else \
                   'Program log: hello'
            return {"blockTime": 1730000000,
                    "meta": {"err": None, "preBalances": [5000000, 0],
                             "postBalances": [4000000, 1000000],
                             "logMessages": [memo]},
                    "transaction": {"message": {"accountKeys": [
                        {"pubkey": "PayerXYZ"}, {"pubkey": _ADDR}]}}}
        return None
    _ex._cache.clear()
    _out = _ex.fetch_activity(_ADDR, limit=2, transport=_rpc_fake)
    check("returns tx rows", len(_out["txs"]) == 2)
    _t = _out["txs"][0]
    check("delta computed for the queried address",
          _t["delta"] == 1000000, str(_t))
    check("veripay memo decoded and flagged",
          _t["memo"].startswith("veripay:paid") and _t["veripay"] is True)
    check("counterparty surfaced", _t["counterparty"] == "PayerXYZ")
    _before = _ncalls["n"]
    _out2 = _ex.fetch_activity(_ADDR, limit=2, transport=_rpc_fake)
    check("second call served from cache", _ncalls["n"] == _before
          and _out2["cached"] is True)
    import server as _sv4
    from fastapi.testclient import TestClient as _TC4
    _c4 = _TC4(_sv4.app)
    check("bad address -> 400 from the endpoint",
          _c4.get("/api/wallet", params={"address": "zz!!"}).status_code == 400)
    _page = _c4.get("/").text
    check("frontend carries the wallet audit section",
          "WALLET_AUDIT" in _page)



# ── 28. OFAC sanctions screening — spec BEFORE code ────────────────────────

print("\n=== 28. sanctions screening ===")
try:
    import screening as _sc
    _has_sc = True
except Exception as _e:
    _has_sc = False
    print(f"  (screening import failed: {_e})")
check("screening module importable", _has_sc)
if _has_sc:
    _BAD = "42RLPACwZPx3vYYmxSueqsogfynBDqXK298EDsNoyoHi"  # real OFAC SDN entry
    _r = _sc.check(_BAD)
    check("listed address flagged with named source and date",
          _r["listed"] is True and "OFAC" in _r["source"] and _r["as_of"])
    _r2 = _sc.check("6BCbkts1TJdvvipwzsebJVfFwuhB6NU4KDPMZQzrjAtz")
    check("unlisted address is NOT called safe — wording is non-coverage",
          _r2["listed"] is False and "safe" not in json.dumps(_r2).lower())
    check("module never claims 'scammer' — attribution wording only",
          "scammer" not in open(os.path.join(os.path.dirname(__file__),
                                             "screening.py")).read().lower())
    # refresh is injectable and failure falls back to the vendored snapshot
    _n0 = len(_sc.addresses())
    _sc.refresh(fetch=lambda: (_ for _ in ()).throw(RuntimeError("net down")))
    check("refresh failure keeps the vendored snapshot",
          len(_sc.addresses()) == _n0)
    _sc.refresh(fetch=lambda: [_BAD, "NewAddr111111111111111111111111111111111111"])
    check("successful refresh replaces the set", len(_sc.addresses()) == 2)
    _sc.reload_vendored()
    # wallet audit surfaces screening for the queried address AND counterparties
    import explorer as _ex2
    def _rpc2(method, params):
        if method == "getSignaturesForAddress":
            return [{"signature": "S1", "blockTime": 1, "err": None}]
        return {"blockTime": 1, "meta": {"err": None,
                "preBalances": [2, 0], "postBalances": [1, 1],
                "logMessages": []},
                "transaction": {"message": {"accountKeys": [
                    {"pubkey": "6BCbkts1TJdvvipwzsebJVfFwuhB6NU4KDPMZQzrjAtz"},
                    {"pubkey": _BAD}]}}}
    _ex2._cache.clear()
    _w = _ex2.fetch_activity("6BCbkts1TJdvvipwzsebJVfFwuhB6NU4KDPMZQzrjAtz",
                             limit=1, transport=_rpc2)
    check("queried-address screening attached",
          _w["sanctions"]["listed"] is False)
    check("sanctioned counterparty raises the red alarm on the tx row",
          _w["txs"][0]["counterparty_sanctioned"] is True)
    # the payment gate refuses a sanctioned recipient BEFORE any tx is built
    import chain as _ch2
    _clean = {"answer": "x", "error": None, "fallback_used": False,
              "checks": [{"passed": True, "error": None}],
              "facts": [{"verified_in_source": True}]}
    try:
        _ch2.pay_if_verified(_clean, _BAD, 1000,
                             keypair=None, transport=lambda **k: {})
        check("sanctioned recipient refused", False)
    except _ch2.PaymentBlocked as e:
        check("sanctioned recipient refused as SANCTIONS_LIST_MATCH",
              "SANCTIONS_LIST_MATCH" in str(e) and "OFAC" in str(e))

    _page28 = _c4.get("/").text if "_c4" in dir() else __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(__import__("server").app).get("/").text
    check("UI renders the sanctions banner wording",
          "SANCTIONED" in _page28 and "OFAC" in _page28)
    check("UI flags sanctioned counterparties on tx rows",
          "counterparty_sanctioned" in _page28)
    check("UI empty state is non-coverage, never 'safe'",
          "no public sanctions reports found" in _page28.lower())


# ── 29. refusal notarization — spec BEFORE code ────────────────────────────

print("\n=== 29. refusal notarization ===")
import chain as _ch3
check("chain exposes notarize_refusal", hasattr(_ch3, "notarize_refusal"))
if hasattr(_ch3, "notarize_refusal"):
    from solders.keypair import Keypair as _KP3
    from solders.transaction import Transaction as _TX3
    import base64 as _b643
    _calls3 = []
    def _fake3(payload):
        _calls3.append(payload)
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "id": 1, "result":
                    {"value": {"blockhash": "1" * 32, "lastValidBlockHeight": 1}}}
        return {"jsonrpc": "2.0", "id": 1, "result": "REFSIG" + "1" * 60}
    _res3 = {"answer": "x", "checks": [{"passed": False, "error": None}],
             "facts": [], "fallback_used": False, "error": None}
    _n = _ch3.notarize_refusal(_res3, "verification failed",
                               keypair=_KP3(), transport=_fake3)
    check("refusal memo carries the digest and the reason marker",
          _n["signature"].startswith("REFSIG"))
    _sent3 = next(c for c in _calls3 if c["method"] == "sendTransaction")
    _tx3 = _TX3.from_bytes(_b643.b64decode(_sent3["params"][0]))
    _memo3 = bytes(_tx3.message.instructions[0].data).decode()
    check("on-chain memo says refused, with sha256",
          _memo3.startswith("veripay:refused:sha256:"))



# ── 30. refusal notarization wired into the live proof (#15) — spec first ──

print("\n=== 30. devnet_live refusal wiring ===")
_dl = open(os.path.join(os.path.dirname(__file__), "scripts",
                        "devnet_live.py")).read()
check("live proof notarizes the refusal", "notarize_refusal" in _dl)
check("refusal leg prints its own explorer line", "REFUSAL-NOTARIZED" in _dl)
check("refusal notarization happens on the REFUSED path, before the paid leg",
      _dl.index("notarize_refusal") < _dl.index("pay_if_verified(clean"))



# ── 31. audit-log root anchored in the notarize memo — spec BEFORE code ────

print("\n=== 31. root anchor ===")
from solders.keypair import Keypair as _KPr
from solders.transaction import Transaction as _TXr
import base64 as _b64r
_callsr = []
def _faker(payload):
    _callsr.append(payload)
    if payload["method"] == "getLatestBlockhash":
        return {"jsonrpc": "2.0", "id": 1, "result":
                {"value": {"blockhash": "1" * 32, "lastValidBlockHeight": 1}}}
    return {"jsonrpc": "2.0", "id": 1, "result": "ROOTSIG" + "1" * 59}
_resr = {"answer": "x", "checks": [], "facts": [], "error": None,
         "fallback_used": False, "audit_log_root": "ab" * 32}
_nr = chain.notarize(_resr, keypair=_KPr(), transport=_faker) if False else None
import chain as _chr
_nr = _chr.notarize(_resr, keypair=_KPr(), transport=_faker)
_sentr = next(c for c in _callsr if c["method"] == "sendTransaction")
_txr = _TXr.from_bytes(_b64r.b64decode(_sentr["params"][0]))
_memor = bytes(_txr.message.instructions[0].data).decode()
check("memo anchors both digests: result and log root",
      ":log:" in _memor and ("ab" * 32) in _memor
      and _memor.startswith("veripay:sha256:"))
check("result digest excludes the root field itself",
      _chr.result_digest(_resr) == _chr.result_digest(
          {k: v for k, v in _resr.items() if k != "audit_log_root"}))
_callsr.clear()
_res_no_root = {k: v for k, v in _resr.items() if k != "audit_log_root"}
_chr.notarize(_res_no_root, keypair=_KPr(), transport=_faker)
_sentr = next(c for c in _callsr if c["method"] == "sendTransaction")
_memor2 = bytes(_TXr.from_bytes(_b64r.b64decode(_sentr["params"][0])).message.instructions[0].data).decode()
check("no root -> memo stays in the original format", ":log:" not in _memor2)



# ── 40. left-rail tabs — spec BEFORE code ──────────────────────────────────

print("\n=== 40. left-rail tabs ===")
_h40 = open(os.path.join(os.path.dirname(__file__), "web",
                         "index.html")).read()
check("DOCUMENT/WALLET tab chrome present",
      'id="tab-doc"' in _h40 and 'id="tab-wallet"' in _h40)
check("wallet markers survive the retheme",
      "WALLET_AUDIT" in _h40 and "SHARE" in _h40
      and "URLSearchParams" in _h40 and "SANCTIONED" in _h40
      and "counterparty_sanctioned" in _h40
      and "no public sanctions reports found" in _h40.lower()
      and "CONTRIBUTION_PROTOCOL" in _h40 and "COPY_ADDRESS" in _h40)

# ── Summary ───────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL:
    print("  WARNING: fix failures before demo day!")
    sys.exit(1)
else:
    print("  All clear. Ship it.")

