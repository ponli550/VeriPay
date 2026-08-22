"""FinVerify backend: parse -> redact -> LLM (DeepSeek, with demo-safety
fallback) -> verify (with anti-hallucination citation check).

Member 1 owns this file. app.py calls exactly one function:

    analyze(file_path: str, question: str) -> dict

Everything else is internal.
"""

import json
import os
import re
import tempfile

import pdfplumber
import openpyxl
from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


# ── Step 1: File parsing (PDF + XLSX) ─────────────────────────────────────

def parse_pdf(path: str) -> list[tuple[str, str]]:
    """Return [("Page 1", text), ...] from a PDF."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((f"Page {i}", text))
    return pages


def pdf_metadata_names(path: str) -> list[str]:
    """Names hiding in PDF document properties. /Author is routinely the
    preparer's real name or a Windows username, and page.extract_text()
    never sees it — so we pull it out and feed it to the redactor as an
    extra known name for this run. PDPA: the file itself never leaves
    the machine, only extracted text does, so scrubbing the text is the
    whole battle."""
    try:
        with pdfplumber.open(path) as pdf:
            meta = pdf.metadata or {}
    except Exception:
        return []
    author = str(meta.get("Author") or "").strip()
    # Ignore junk values and software names ("Microsoft Word") — a lone
    # word with no space is almost never a redactable full name.
    if len(author) >= 4 and " " in author:
        return [author]
    return []


def parse_xlsx(path: str) -> list[tuple[str, str]]:
    """Return [("Sheet: name", text), ...] from an Excel file."""
    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = []
    for name in wb.sheetnames:
        ws = wb[name]
        lines = []
        for row in ws.iter_rows(values_only=True):
            line = "\t".join(str(c) if c is not None else "" for c in row)
            if line.strip():
                lines.append(line)
        if lines:
            sheets.append((f"Sheet: {name}", "\n".join(lines)))
    wb.close()
    return sheets


def parse_file(path: str) -> list[tuple[str, str]]:
    """Route to the right parser based on extension."""
    low = path.lower()
    if low.endswith(".xlsx") or low.endswith(".xls"):
        return parse_xlsx(path)
    if low.endswith(".pdf"):
        return parse_pdf(path)
    raise ValueError(f"Unsupported file type: {os.path.splitext(path)[1]}")


# ── Step 2: PII redaction (runs BEFORE anything reaches the model) ────────

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MY_IC_RE = re.compile(r"\b\d{6}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b(?:\+?60|0)1\d[-\s]?\d{3,4}[-\s]?\d{4}\b")

# NOTE: no bare 12-digit rule on purpose. Malaysian company registration
# numbers under the Companies Act 2016 are 12 digits (YYYYNNNNNNNN) and
# sit on the cover of every set of financial statements — redacting them
# destroys the identity of the entity being audited, and they are not
# personal data.

# Name detection, layered (regex alone cannot catch names — say this
# limitation out loud in the pitch; it reads as honest, not weak):
#   1. KNOWN_NAMES        — hand-listed names for the demo document
#   2. PDF /Author        — pulled from metadata per-run (see analyze)
#   3. "Prepared by" cues — signature-block lines name the preparer
#   4. Honorific + name   — Datuk/Dato'/Tan Sri/Encik/Puan/Mr/Dr...
#   5. bin/binti, a/l a/p — Malay & Indian patronymics, any casing,
#                            which cover ALL-CAPS signature blocks
# Residual gap: a bare Chinese or Western name with no title, cue, or
# patronymic (e.g. "LIM CHEE KEONG" alone on a line) is NOT caught.
KNOWN_NAMES: list[str] = [
    "Ahmad Bin Ali",
]

_NAME_WORD = r"[A-Z][\w'.@-]*"
HONORIFIC_RE = re.compile(
    r"\b(?:Mr|Ms|Mrs|Dr|Ir|Tun|Tan\s+Sri|Puan\s+Sri|Toh\s+Puan|Datuk\s+Seri|"
    r"Dato'?\s+Sri|Datuk|Dato'?|Datin|Encik|Puan|Cik|Tuan|Haji|Hajah)\.?\s+"
    rf"{_NAME_WORD}(?:\s+(?:{_NAME_WORD}|bin|binti|a/l|a/p)){{0,4}}"
)
CUE_RE = re.compile(
    r"\b((?:Prepared|Reviewed|Approved|Signed|Certified|Audited)\s+by[:\s]+)"
    rf"({_NAME_WORD}(?:\s+(?:{_NAME_WORD}|bin|binti|a/l|a/p)){{0,4}})"
)
PATRONYMIC_RE = re.compile(
    r"\b[A-Za-z][\w'@-]*(?:\s+[A-Za-z][\w'@-]*)*"
    r"\s+(?:bin|binti|a/l|a/p)\s+"
    r"[A-Za-z][\w'@-]*(?:\s+[A-Za-z][\w'@-]*)*\b",
    re.IGNORECASE,
)


def _valid_ic_date(match: re.Match) -> bool:
    """First six NRIC digits must be a plausible YYMMDD — this is what
    keeps invoice/reference numbers shaped like 123456-78-9012 from
    being falsely redacted."""
    digits = match.group(0)
    month, day = int(digits[2:4]), int(digits[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def redact(text: str, extra_names: list[str] | None = None) -> tuple[str, int]:
    """Scrub PII. Returns (clean_text, items_redacted)."""
    count = 0

    for name in [*KNOWN_NAMES, *(extra_names or [])]:
        text, n = re.subn(re.escape(name), "[NAME_REDACTED]", text, flags=re.IGNORECASE)
        count += n

    text, n = CUE_RE.subn(lambda m: m.group(1) + "[NAME_REDACTED]", text)
    count += n
    text, n = HONORIFIC_RE.subn("[NAME_REDACTED]", text)
    count += n
    text, n = PATRONYMIC_RE.subn("[NAME_REDACTED]", text)
    count += n

    # NRIC with date validation: only real YYMMDD prefixes are redacted.
    ic_hits = 0

    def _ic_repl(m: re.Match) -> str:
        nonlocal ic_hits
        if _valid_ic_date(m):
            ic_hits += 1
            return "[IC_REDACTED]"
        return m.group(0)

    text = MY_IC_RE.sub(_ic_repl, text)
    count += ic_hits

    for pattern, token in (
        (EMAIL_RE, "[EMAIL_REDACTED]"),
        (PHONE_RE, "[PHONE_REDACTED]"),
    ):
        text, n = pattern.subn(token, text)
        count += n
    return text, count


# ── Step 3: LLM call (DeepSeek, strict JSON, demo-safety fallback) ─────────

SYSTEM_PROMPT = """You analyse financial reports. Reply with a single JSON
object and nothing else.

Schema:
{
  "answer": "your natural-language answer to the user's question",
  "recommendation": "one concrete, actionable next step for the reader, grounded ONLY in verified findings",
  "facts": [
    {"id": "f1", "claim": "short label", "value": 1200000,
     "page": "Page 1", "quote": "the exact line copied from the document"}
  ],
  "risks": [
    {"description": "the risk in plain language", "severity": "low|medium|high",
     "evidence_fact_ids": ["f1", "f4"]}
  ],
  "checks": [
    {"description": "what this verifies",
     "operation": "sum",
     "operand_fact_ids": ["f1", "f2"],
     "against_fact_id": "f4",
     "expected_value": 2400000}
  ]
}

Rules:
- "value" and "expected_value" must be plain numbers with no commas, no
  currency symbols, and no quotes around them.
- "operation" must be exactly one of: sum, difference, percent_change.
- Every number you state in "answer" MUST appear in "facts" with the real
  page or sheet name and a quote copied verbatim from the document.
- Whenever your answer relies on arithmetic, add a check for it. If the
  document states a total, ALWAYS add a check comparing it to the sum of
  its line items.
- List trends, exceptions, and potential risks in "risks". Every risk MUST
  cite evidence_fact_ids pointing at entries in "facts" — a risk with no
  evidence will be discarded.
- When the document contains more than one period, ALWAYS add a
  percent_change check for each key metric using operand_fact_ids
  [prior_period_fact, current_period_fact]; if the document states the
  growth rate, reference that fact with "against_fact_id".
- "against_fact_id" must reference the fact holding the figure the
  DOCUMENT states (e.g. the stated total). Never set "expected_value"
  to a number you computed yourself — the comparison target must be a
  figure from the document, so a wrong stated total is caught rather
  than reproduced.
- Never invent a quote. If a number is not in the document, say so.
- "recommendation" must be a single sentence a finance team could act on
  (e.g. reconcile a mismatched total before sign-off). If nothing needs
  action, say the figures verified cleanly.
"""


def _extract_json(raw: str) -> dict:
    """Pull a JSON object out of model output, tolerating fences/preamble."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _call_deepseek(pages: list[tuple[str, str]], question: str) -> dict:
    """Raw call to DeepSeek (OpenAI-compatible API). Raises on ANY
    failure: missing dependency, missing API key, network error, or
    unparsable output. Deliberately has no fallback logic of its own —
    ask_llm() owns that, so a missing key is caught by the same safety
    net as a live network failure."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai is not installed. Run: uv add openai")

    # DEEPSEEK_API_KEY is canonical; `deepseek_api` accepted so an
    # existing .env keeps working without edits.
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("deepseek_api")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Get a key at platform.deepseek.com "
            "and put it in your .env file."
        )

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    doc = "\n\n".join(f"--- {label} ---\n{text}" for label, text in pages)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"DOCUMENT:\n{doc}\n\nQUESTION: {question}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    return _extract_json(resp.choices[0].message.content)


def _load_cached_response() -> dict:
    """Load the demo-safety cached response used when DEMO_FALLBACK=1."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "cached_response.json")
    with open(path) as fh:
        return json.load(fh)


def ask_llm(pages: list[tuple[str, str]], question: str) -> tuple[dict, bool]:
    """Send redacted document to DeepSeek. Returns (parsed JSON dict, cached).

    `cached` is True only when the DEMO_FALLBACK substitution fired — the
    UI shows an amber banner in that case, so a replayed fixture can never
    silently impersonate a live extraction.

    If DEMO_FALLBACK=1 in the environment, ANY failure from
    _call_deepseek() — including a missing API key, not just a live
    network error — is caught and a cached response is returned instead
    of raising. This is NOT general retry logic: it is a single, narrow
    substitution for one demo document, meant to be enabled only in the
    few minutes before walking on stage. DEMO_FALLBACK must stay "0"
    during normal development so real failures still look like real
    failures.
    """
    try:
        return _call_deepseek(pages, question), False
    except Exception as e:
        if os.environ.get("DEMO_FALLBACK") == "1":
            return _load_cached_response(), True
        raise RuntimeError(f"Model call failed: {e}")


# ── Step 4: Deterministic verification (no eval, ever) ────────────────────

def _to_number(value):
    """Coerce model output into a float, or None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.\-]", "", value)
        if cleaned not in ("", "-", ".", "-."):
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def verify(facts: list, checks: list) -> list[dict]:
    """Independently recompute every calculation the model asserted."""
    by_id = {}
    for f in facts or []:
        if isinstance(f, dict) and f.get("id") is not None:
            by_id[str(f["id"])] = f

    results = []
    for check in checks or []:
        if not isinstance(check, dict):
            continue

        row = {
            "description": check.get("description", "(no description)"),
            "expected": _to_number(check.get("expected_value")),
            "actual": None,
            "passed": False,
            "error": None,
        }

        # A cited fact beats a free-typed expected_value: the model once
        # set expected_value to its own computed sum, turning a real
        # discrepancy into a PASS. A fact id points at a value that the
        # citation check independently pins to the source text.
        against = check.get("against_fact_id")
        if against is not None:
            target = by_id.get(str(against))
            target_val = _to_number(target.get("value")) if isinstance(target, dict) else None
            if target_val is None:
                row["error"] = f"against_fact_id references unknown fact: {against}"
                results.append(row)
                continue
            row["expected"] = target_val

        ids = check.get("operand_fact_ids") or []
        if not isinstance(ids, list) or not ids:
            row["error"] = "check listed no facts to verify against"
            results.append(row)
            continue

        values, missing = [], []
        for fid in ids:
            fact = by_id.get(str(fid))
            num = _to_number(fact.get("value")) if isinstance(fact, dict) else None
            if num is None:
                missing.append(str(fid))
            else:
                values.append(num)

        if missing:
            row["error"] = f"referenced unusable fact(s): {', '.join(missing)}"
            results.append(row)
            continue

        op = str(check.get("operation", "")).lower()
        if op == "sum":
            row["actual"] = sum(values)
        elif op == "difference":
            row["actual"] = values[0] - sum(values[1:])
        elif op == "percent_change":
            if len(values) < 2:
                row["error"] = "percent_change needs two facts"
            elif values[0] == 0:
                row["error"] = "cannot compute percent change from zero"
            else:
                row["actual"] = round(
                    (values[1] - values[0]) / values[0] * 100, 2
                )
        else:
            row["error"] = f"unsupported operation: {op or '(missing)'}"

        if row["error"] is None and row["expected"] is None:
            row["error"] = "model did not state an expected value"

        if row["error"] is None and row["actual"] is not None:
            row["passed"] = abs(row["actual"] - row["expected"]) < 0.01

        results.append(row)

    return results


# ── Step 4b: Anti-hallucination citation check ─────────────────────────────

def _fact_in_source(fact: dict, redacted_pages: list[tuple[str, str]]) -> bool:
    """True if this fact's quote actually appears, verbatim, in a page it
    could plausibly cite. This is what lets the UI show a genuine warning
    when the model paraphrased instead of quoting — a stronger
    explainability claim than a page number alone."""
    quote = (fact.get("quote") or "").strip()
    if not quote:
        return False
    return any(quote in text for _, text in redacted_pages)


# ── Step 4c: Session hygiene ───────────────────────────────────────────────

def purge_upload(path: str | None) -> bool:
    """Delete an uploaded temp file — but ONLY if it lives inside Gradio's
    temp directory. A path anywhere else (the user's own document, our
    sample_report.pdf) is refused: this function must never be able to
    destroy a file the user did not stage through the upload box.
    Wired to the UI's Clear Session button — that is what makes the
    retention claim in the interface true rather than aspirational."""
    if not path:
        return False
    gradio_tmp = os.environ.get("GRADIO_TEMP_DIR") or os.path.join(
        tempfile.gettempdir(), "gradio"
    )
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(gradio_tmp)
        if real.startswith(root + os.sep) and os.path.isfile(real):
            os.remove(real)
            return True
    except OSError:
        pass
    return False


# ── Step 5: Main entry point ──────────────────────────────────────────────

def _clean_facts(facts) -> list[dict]:
    """Normalise facts so the UI never crashes on weird model output."""
    out = []
    for f in facts or []:
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "id": str(f.get("id", "?")),
                "claim": str(f.get("claim", "(unlabelled)")),
                "value": _to_number(f.get("value")),
                "page": str(f.get("page", "?")),
                "quote": str(f.get("quote", "")).strip(),
            }
        )
    return out


PIPELINE_STAGES = ["parse", "redact", "extract",
                   "verify-math", "verify-citations", "release"]


def analyze_stream(file_path: str, question: str):
    """In-product CI: the pipeline the user watches. Yields one event per
    stage transition — {"stage", "status": running|ok|fail|skip,
    "detail", "elapsed_ms", "result"} — and releases the result only in
    the final event. Nothing upstream carries the answer: what the user
    sees has, by construction, already passed verification. Failures are
    released as failures, never hidden."""
    import time as _time

    result = _empty_result()
    failed = False

    def _stage(name):
        return {"stage": name, "status": "running", "detail": "",
                "elapsed_ms": None, "result": None}

    # ── parse ──────────────────────────────────────────────────────────
    ev = _stage("parse")
    yield ev
    t0 = _time.perf_counter()
    pages = []
    if not file_path:
        result["error"] = "No file provided."
    elif not question or not question.strip():
        result["error"] = "Ask a question about the document."
    else:
        try:
            pages = parse_file(file_path)
            if not pages:
                result["error"] = ("No text found. If this is a scanned "
                                   "PDF, try a text-based one.")
        except Exception as e:
            result["error"] = f"Could not read that file: {e}"
    ms = int((_time.perf_counter() - t0) * 1000)
    if result["error"]:
        failed = True
        yield {"stage": "parse", "status": "fail", "detail": result["error"],
               "elapsed_ms": ms, "result": None}
    else:
        yield {"stage": "parse", "status": "ok",
               "detail": f"{len(pages)} page(s) of text",
               "elapsed_ms": ms, "result": None}

    # ── redact ─────────────────────────────────────────────────────────
    redacted = []
    if failed:
        yield {"stage": "redact", "status": "skip", "detail": "",
               "elapsed_ms": None, "result": None}
    else:
        ev = _stage("redact")
        yield ev
        t0 = _time.perf_counter()
        extra_names = (pdf_metadata_names(file_path)
                       if file_path.lower().endswith(".pdf") else [])
        total = 0
        for label, text in pages:
            clean, n = redact(text, extra_names)
            redacted.append((label, clean))
            total += n
        result["redaction_count"] = total
        result["redacted_preview"] = "\n\n".join(
            f"--- {label} ---\n{text}" for label, text in redacted
        )[:2000]
        ms = int((_time.perf_counter() - t0) * 1000)
        yield {"stage": "redact", "status": "ok",
               "detail": f"{total} PII item(s) masked before transmission",
               "elapsed_ms": ms, "result": None}

    # ── extract ────────────────────────────────────────────────────────
    raw = None
    if failed:
        yield {"stage": "extract", "status": "skip", "detail": "",
               "elapsed_ms": None, "result": None}
    else:
        ev = _stage("extract")
        yield ev
        t0 = _time.perf_counter()
        try:
            raw, cached = ask_llm(redacted, question.strip())
            result["fallback_used"] = cached
            if not isinstance(raw, dict):
                result["error"] = "Model returned an unexpected shape."
        except Exception as e:
            result["error"] = str(e)
        ms = int((_time.perf_counter() - t0) * 1000)
        if result["error"]:
            failed = True
            yield {"stage": "extract", "status": "fail",
                   "detail": result["error"], "elapsed_ms": ms, "result": None}
        else:
            src_label = "CACHED fixture" if result["fallback_used"] else "LIVE model call"
            n_facts = len(raw.get("facts") or [])
            yield {"stage": "extract", "status": "ok",
                   "detail": f"{n_facts} fact(s) via {src_label}",
                   "elapsed_ms": ms, "result": None}

    # ── verify-math ────────────────────────────────────────────────────
    if failed:
        yield {"stage": "verify-math", "status": "skip", "detail": "",
               "elapsed_ms": None, "result": None}
    else:
        ev = _stage("verify-math")
        yield ev
        t0 = _time.perf_counter()
        result["answer"] = str(raw.get("answer", "")).strip()
        result["recommendation"] = str(raw.get("recommendation", "")).strip()
        result["facts"] = _clean_facts(raw.get("facts"))
        result["risks"] = _clean_risks(raw.get("risks"), result["facts"])
        result["checks"] = verify(result["facts"], raw.get("checks"))
        checks = result["checks"]
        result["summary"] = {
            "facts_extracted": len(result["facts"]),
            "checks_run": len(checks),
            "checks_passed": sum(1 for c in checks if c.get("passed")),
            "checks_failed": sum(
                1 for c in checks if not c.get("passed") and not c.get("error")
            ),
        }
        ms = int((_time.perf_counter() - t0) * 1000)
        s = result["summary"]
        yield {"stage": "verify-math", "status": "ok",
               "detail": (f"{s['checks_run']} check(s): "
                          f"{s['checks_passed']} pass, "
                          f"{s['checks_failed']} mismatch"),
               "elapsed_ms": ms, "result": None}

    # ── verify-citations ───────────────────────────────────────────────
    if failed:
        yield {"stage": "verify-citations", "status": "skip", "detail": "",
               "elapsed_ms": None, "result": None}
    else:
        ev = _stage("verify-citations")
        yield ev
        t0 = _time.perf_counter()
        for f in result["facts"]:
            f["verified_in_source"] = _fact_in_source(f, redacted)
        hits = sum(1 for f in result["facts"] if f["verified_in_source"])
        ms = int((_time.perf_counter() - t0) * 1000)
        yield {"stage": "verify-citations", "status": "ok",
               "detail": f"{hits}/{len(result['facts'])} quote(s) verbatim in source",
               "elapsed_ms": ms, "result": None}

    # ── release ────────────────────────────────────────────────────────
    yield {"stage": "release",
           "status": "fail" if failed else "ok",
           "detail": "released with error" if failed else "verified output released",
           "elapsed_ms": 0, "result": result}


def _empty_result() -> dict:
    return {
        "answer": "",
        "recommendation": "",
        "risks": [],
        "facts": [],
        "risks": [
    {"description": "the risk in plain language", "severity": "low|medium|high",
     "evidence_fact_ids": ["f1", "f4"]}
  ],
  "checks": [],
        "redaction_count": 0,
        "redacted_preview": "",
        "fallback_used": False,
        "summary": {"facts_extracted": 0, "checks_run": 0,
                    "checks_passed": 0, "checks_failed": 0},
        "error": None,
    }


def _clean_risks(risks, facts) -> list[dict]:
    """Keep only risks whose EVERY evidence id resolves to an extracted
    fact — an uncited or phantom-cited risk is an unsupported claim and
    does not reach the user. Explainability, enforced."""
    known = {str(f.get("id")) for f in facts or []}
    out = []
    for r in risks or []:
        if not isinstance(r, dict):
            continue
        ids = [str(i) for i in (r.get("evidence_fact_ids") or [])]
        if not ids or not set(ids) <= known:
            continue
        sev = str(r.get("severity", "medium")).lower()
        out.append({
            "description": str(r.get("description", "")).strip(),
            "severity": sev if sev in ("low", "medium", "high") else "medium",
            "evidence_fact_ids": ids,
        })
    return out


def analyze(file_path: str, question: str) -> dict:
    """Full pipeline. Never raises — errors come back in the 'error' field.
    Thin consumer of analyze_stream(): one code path, two presentations."""
    result = None
    for event in analyze_stream(file_path, question):
        if event["stage"] == "release":
            result = event["result"]
    return result
