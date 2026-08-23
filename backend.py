"""FinVerify backend: parse -> redact -> LLM (DeepSeek, with demo-safety
fallback) -> verify (with anti-hallucination citation check).

Member 1 owns this file. app.py calls exactly one function:

    analyze(file_path: str, question: str) -> dict

Everything else is internal.
"""

import json
import os
import random
import re
import tempfile
import time

import pdfplumber
import openpyxl
from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.2")


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
#   1. KNOWN_NAMES        — extra names supplied at runtime (see below)
#   2. PDF /Author        — pulled from metadata per-run (see analyze)
#   3. "Prepared by" cues — signature-block lines name the preparer
#   4. Honorific + name   — Datuk/Dato'/Tan Sri/Encik/Puan/Mr/Dr...
#   5. bin/binti, a/l a/p — Malay & Indian patronymics, any casing,
#                            which cover ALL-CAPS signature blocks
# Residual gap: a bare Chinese or Western name with no title, cue, or
# patronymic (e.g. "LIM CHEE KEONG" alone on a line) is NOT caught.
#
# KNOWN_NAMES is EMPTY by default — no personal name is baked into the
# source. The demo preparer ("Prepared by Ahmad Bin Ali") is already
# caught by the cue and patronymic layers, so no hardcode is needed. If a
# deployment needs to force-scrub specific names, set FINVERIFY_KNOWN_NAMES
# to a comma-separated list; it is read at import time only.
KNOWN_NAMES: list[str] = [
    n.strip() for n in os.environ.get("FINVERIFY_KNOWN_NAMES", "").split(",")
    if n.strip()
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
  "charts": [
    {"kind": "any visualization kind you judge best", "title": "short title",
     "points": [{"fact_id": "f1", "label": "optional override"}]}
  ],
  "risks": [
    {"description": "the risk in plain language", "severity": "low|medium|high",
     "evidence_fact_ids": ["f1", "f4"]}
  ],
  "patterns": [
    {"kind": "composition",
     "description": "what this pattern says in plain language",
     "operand_fact_ids": ["f1"],
     "against_fact_id": "f4",
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
- In "charts", propose whichever visualization best reveals the data —
  any kind you like (bar, line, donut, waterfall, anything). Be
  inventive. But points may ONLY reference fact ids from "facts";
  invented numbers are discarded, and only verified facts are drawn.
- List trends, exceptions, and potential risks in "risks". Every risk MUST
  cite evidence_fact_ids pointing at entries in "facts" — a risk with no
  evidence will be discarded.
- Surface noteworthy PATTERNS in "patterns". "kind" must be exactly one of:
  composition, ratio, sign. Each pattern MUST cite evidence_fact_ids that
  point at entries in "facts" — a pattern with no evidence is discarded,
  and its arithmetic is recomputed in Python, so do not compute it yourself.
    * composition — a line item's share of a total. Put the line item in
      operand_fact_ids and the total in against_fact_id (e.g. product
      revenue as a share of total revenue).
    * ratio — one figure over another as a percentage. Put the two facts
      in operand_fact_ids [numerator, denominator]. ALWAYS include these
      standard financial ratios when the facts exist: opex-to-revenue
      (total operating expenses / total revenue) and, if cost of sales
      or COGS is present, gross margin ((revenue - COGS) / revenue via a
      gross-profit fact / revenue).
    * sign — a figure that is negative or zero where a positive is
      expected (e.g. a negative net profit). Put that one fact in
      operand_fact_ids.
  Do NOT put a number inside a pattern "description"; describe the pattern
  in words only — any digit you type there will be masked out.
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
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _openai_style(base_url: str, model: str, api_key: str,
                  pages: list[tuple[str, str]], question: str) -> dict:
    """Shared path for OpenAI-compatible chat endpoints (DeepSeek, Google's
    official Gemini compatibility endpoint, OpenAI itself)."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=45)
    doc = "\n\n".join(f"--- {label} ---\n{text}" for label, text in pages)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"DOCUMENT:\n{doc}\n\nQUESTION: {question}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    return _extract_json(resp.choices[0].message.content)


def _call_gemini(pages, question, api_key=None):
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set — supply your own key "
                           "(aistudio.google.com) in the Connect-your-AI field.")
    return _openai_style(
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        GEMINI_MODEL, key, pages, question)


def _call_openai(pages, question, api_key=None):
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set — supply your own key "
                           "(platform.openai.com) in the Connect-your-AI field.")
    return _openai_style("https://api.openai.com/v1", OPENAI_MODEL, key,
                         pages, question)


def _call_claude(pages, question, api_key=None):
    """Official anthropic SDK — never an OpenAI-compat shim for Claude."""
    import anthropic
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — supply your own "
                           "key (console.anthropic.com) in the Connect-your-AI field.")
    client = anthropic.Anthropic(api_key=key, timeout=60.0)
    doc = "\n\n".join(f"--- {label} ---\n{text}" for label, text in pages)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": f"DOCUMENT:\n{doc}\n\nQUESTION: {question}"}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def _call_deepseek(pages: list[tuple[str, str]], question: str,
                   api_key: str | None = None) -> dict:
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
    api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY")
               or os.environ.get("deepseek_api"))
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Get a key at platform.deepseek.com "
            "and put it in your .env file."
        )

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=45)
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


# BYOK registry: user-supplied keys ride one request and are never
# stored or logged — retention = request, same as the uploads.
PROVIDERS = {
    "deepseek": lambda p, q, k=None: _call_deepseek(p, q, k),
    "gemini":   lambda p, q, k=None: _call_gemini(p, q, k),
    "claude":   lambda p, q, k=None: _call_claude(p, q, k),
    "openai":   lambda p, q, k=None: _call_openai(p, q, k),
}


def _dispatch(provider, pages, question, api_key):
    impl = PROVIDERS.get(provider)
    if impl is None:
        raise RuntimeError(
            f"unknown provider: {provider} — supported: {sorted(PROVIDERS)}")
    if api_key is None and provider == "deepseek":
        return _call_deepseek(pages, question)   # env-key path, test-compatible
    return impl(pages, question, api_key)


def _load_cached_response() -> dict:
    """Load the demo-safety cached response used when DEMO_FALLBACK=1."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "cached_response.json")
    # Explicit UTF-8: the fixture contains em-dashes and other non-ASCII
    # punctuation, and Python's default encoding on Windows is cp1252,
    # which mangles them into mojibake ("— " -> "â€"").
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def ask_llm(pages: list[tuple[str, str]], question: str,
            provider: str | None = None,
            api_key: str | None = None) -> tuple[dict, bool]:
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
    provider = (provider or os.environ.get("LLM_PROVIDER") or "deepseek").lower()
    if provider not in PROVIDERS:
        raise RuntimeError(
            f"unknown provider: {provider} — supported: {sorted(PROVIDERS)}")
    try:
        return _dispatch(provider, pages, question, api_key), False
    except Exception:
        # One bounded retry with jitter: hotspot blips and transient 5xx
        # are common; a second attempt is cheap. Anything past that is a
        # real outage and belongs to the fallback/raise path.
        time.sleep(random.uniform(0.3, 0.9))
        try:
            return _dispatch(provider, pages, question, api_key), False
        except Exception as e:
            if os.environ.get("DEMO_FALLBACK") == "1":
                return _load_cached_response(), True
            raise RuntimeError(f"Model call failed after retry: {e}")


# ── Step 4: Deterministic verification (no eval, ever) ────────────────────

def _to_number(value):
    """Coerce model output into a float, or None.

    Financial statements write negatives in parentheses — "(50,000)"
    means -50,000. We detect the brackets BEFORE stripping punctuation,
    because the regex below would otherwise discard them and turn a loss
    into a gain (a real correctness hazard for a verifier)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        # Accounting-style negative: a fully bracketed number.
        negate = bool(re.fullmatch(r"\(\s*[\d,.\s]+\s*\)", s))
        cleaned = re.sub(r"[^\d.\-]", "", s)
        if cleaned not in ("", "-", ".", "-."):
            try:
                num = float(cleaned)
            except ValueError:
                return None
            return -abs(num) if negate else num
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
            "operation": str(check.get("operation", "")).lower(),
            "expected": _to_number(check.get("expected_value")),
            "actual": None,
            "passed": False,
            "error": None,
            # Display-only trust-story fields. "model_stated" preserves the
            # number the MODEL typed as expected_value; "overridden" is True
            # when the anti-cheat replaced it with a different figure pinned
            # to a cited fact. Neither affects "expected" or "passed" — they
            # exist purely so the UI can show "the model claimed X, we
            # ignored it and recomputed against Y".
            "model_stated": _to_number(check.get("expected_value")),
            "overridden": False,
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
            # Record the override for the trust story: the model typed one
            # number, but we are using the cited fact's figure instead.
            if (row["model_stated"] is not None
                    and abs(row["model_stated"] - target_val) >= 0.01):
                row["overridden"] = True
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
        # Pattern operations (composition/ratio/sign) express a computed
        # RELATIONSHIP rather than a document-stated total to reconcile.
        # They are recomputed in Python from cited facts exactly like the
        # reconciliation operations, so the "recompute, never trust"
        # discipline is identical; they just don't always require a
        # model-stated expected value.
        is_pattern_op = op in ("composition", "ratio", "sign")
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
        elif op == "composition":
            # Share of the FIRST operand within the total held by the
            # against-fact (preferred) or the sum of all operands. The
            # against-fact here is the DENOMINATOR (the total), not a
            # reconciliation target, so consume it and clear "expected"
            # — the pattern stands on its recomputed share, it is not
            # being checked against a stated share.
            total = (row["expected"] if against is not None
                     else sum(values))
            row["expected"] = None
            if not total:
                row["error"] = "cannot compute a share of a zero total"
            else:
                row["actual"] = round(values[0] / total * 100, 2)
        elif op == "ratio":
            if len(values) < 2:
                row["error"] = "ratio needs two facts"
            elif values[1] == 0:
                row["error"] = "cannot compute a ratio over zero"
            else:
                row["actual"] = round(values[0] / values[1] * 100, 2)
        elif op == "sign":
            # A threshold/sign pattern: the fact's own sign IS the
            # finding. actual = the value; passed means it is negative
            # (i.e. the flagged condition genuinely holds in the data).
            row["actual"] = values[0]
        else:
            row["error"] = f"unsupported operation: {op or '(missing)'}"

        if op == "sign":
            # No expected value to reconcile: the pattern is confirmed
            # when the value is actually negative/zero as flagged.
            if row["error"] is None:
                row["passed"] = row["actual"] <= 0
        elif is_pattern_op:
            # composition/ratio: if the model cited an against-fact or
            # typed an expected share, reconcile against it; otherwise the
            # recomputed relationship stands on its own as confirmed.
            if row["error"] is None:
                if row["expected"] is not None and row["actual"] is not None:
                    row["passed"] = abs(row["actual"] - row["expected"]) < 0.01
                elif row["actual"] is not None:
                    row["passed"] = True
        else:
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


_PATTERN_KINDS = ("composition", "ratio", "sign")
_KIND_TO_OP = {"composition": "composition", "ratio": "ratio",
               "sign": "sign", "threshold": "sign"}


def verify_patterns(facts: list, patterns: list) -> list[dict]:
    """Recompute each model-proposed pattern in Python from its cited
    facts, using the SAME verify() recompute path as reconciliation
    checks — so patterns inherit the 'never trust a model number'
    discipline, including the against_fact_id anti-cheat rule.

    A pattern is DROPPED (never shown) unless every one of its
    evidence_fact_ids resolves to an extracted fact, mirroring the
    evidence discipline in _clean_risks. A pattern whose arithmetic
    cannot be recomputed comes back with an 'error' set and is surfaced
    as unverifiable, never as an established finding.

    Returns rows shaped like verify() output, each tagged with 'kind'
    and 'description'."""
    known = {str(f.get("id")) for f in facts or []}
    rows = []
    for p in patterns or []:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("kind", "")).lower()
        op = _KIND_TO_OP.get(kind)
        if op is None:
            continue  # unknown pattern kind — discard, don't guess
        ev = [str(i) for i in (p.get("evidence_fact_ids") or [])]
        if not ev or not set(ev) <= known:
            continue  # uncited or phantom-cited — unsupported claim
        # Map the pattern onto a check and run it through verify().
        check = {
            "description": p.get("description", "(pattern)"),
            "operation": op,
            "operand_fact_ids": p.get("operand_fact_ids") or [],
            "against_fact_id": p.get("against_fact_id"),
            "expected_value": p.get("expected_value"),
        }
        row = verify(facts, [check])[0]
        row["kind"] = kind if kind in _PATTERN_KINDS else "threshold"
        row["evidence_fact_ids"] = ev
        rows.append(row)
    return rows


def _audit_key() -> bytes:
    """Server-held HMAC key. Explicit via AUDIT_HMAC_KEY; otherwise a
    per-process random key — signatures then prove integrity within a
    session, which is exactly the retention story (nothing persists)."""
    k = os.environ.get("AUDIT_HMAC_KEY")
    if k:
        return k.encode()
    global _EPHEMERAL_AUDIT_KEY
    try:
        return _EPHEMERAL_AUDIT_KEY
    except NameError:
        import secrets
        _EPHEMERAL_AUDIT_KEY = secrets.token_bytes(32)
        return _EPHEMERAL_AUDIT_KEY


def _event_canonical(event: dict) -> str:
    """Canonical form of an event for hashing: hash-fields stripped, the
    release result folded to its own digest so the chain covers it
    without embedding megabytes."""
    import hashlib
    row = {k: v for k, v in event.items()
           if k not in ("prev_hash", "row_hash", "sig")}
    if row.get("result") is not None:
        body = {k: v for k, v in row["result"].items()
                if k != "audit_log_root"}
        row["result"] = hashlib.sha256(
            json.dumps(body, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _chain_events(events):
    """Wrap raw pipeline events into a hash chain: row_hash =
    sha256(prev_hash + canonical(row)), sig = HMAC(key, row_hash)."""
    import hashlib
    import hmac as hmac_lib
    prev = ""
    key = _audit_key()
    for event in events:
        row_hash = hashlib.sha256(
            (prev + _event_canonical(event)).encode()).hexdigest()
        if event.get("stage") == "release" and event.get("result") is not None:
            # The chain root IS this row's hash; it cannot cover itself,
            # so canonicalization strips the field before hashing and we
            # stamp it afterwards. Verification strips it identically.
            event["result"]["audit_log_root"] = row_hash
        event["prev_hash"] = prev
        event["row_hash"] = row_hash
        event["sig"] = hmac_lib.new(key, row_hash.encode(),
                                    hashlib.sha256).hexdigest()
        prev = row_hash
        yield event


def verify_audit_chain(events) -> bool:
    """True iff the chain is intact: every row_hash recomputes from its
    predecessor and every sig verifies. Any mutation or reorder fails."""
    import hashlib
    import hmac as hmac_lib
    key = _audit_key()
    prev = ""
    for event in events:
        if event.get("prev_hash") != prev:
            return False
        expected = hashlib.sha256(
            (prev + _event_canonical(event)).encode()).hexdigest()
        if event.get("row_hash") != expected:
            return False
        if not hmac_lib.compare_digest(
                event.get("sig", ""),
                hmac_lib.new(key, expected.encode(),
                             hashlib.sha256).hexdigest()):
            return False
        prev = expected
    return True


PIPELINE_STAGES = ["parse", "redact", "extract",
                   "verify-math", "verify-citations",
                   "analyse-patterns", "release"]


def analyze_stream(file_path: str, question: str,
                   provider: str | None = None,
                   api_key: str | None = None):
    """Chained public face of the pipeline: every event the user watches
    is hash-chained and HMAC-signed (see verify_audit_chain), so the
    telemetry itself is tamper-evident — the PDPA story, enforced rather
    than labeled."""
    yield from _chain_events(_analyze_events(file_path, question,
                                         provider, api_key))


def _analyze_events(file_path: str, question: str,
                    provider: str | None = None,
                    api_key: str | None = None):
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
            _prov = (provider or os.environ.get("LLM_PROVIDER")
                     or "deepseek").lower()
            result["provider"] = _prov
            raw, cached = ask_llm(redacted, question.strip(),
                                  provider=_prov, api_key=api_key)
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
            src_label = ("CACHED fixture" if result["fallback_used"]
                         else f"LIVE {result.get('provider', 'model')} call")
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
        _raw_risks = [r for r in (raw.get("risks") or []) if isinstance(r, dict)]
        result["risks"] = _clean_risks(raw.get("risks"), result["facts"])
        _risks_dropped = len(_raw_risks) - len(result["risks"])
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
        # First pass at insights from checks+risks; rebuilt after the
        # analyse-patterns stage so verified patterns join the summary.
        result["insights"] = build_insights(
            result["facts"], checks, result["risks"], result["summary"],
            result["patterns"]
        )
        ms = int((_time.perf_counter() - t0) * 1000)
        s = result["summary"]
        _sup = (f"; suppressed {_risks_dropped} unsupported risk(s)"
                if _risks_dropped > 0 else "")
        yield {"stage": "verify-math", "status": "ok",
               "detail": (f"{s['checks_run']} check(s): "
                          f"{s['checks_passed']} pass, "
                          f"{s['checks_failed']} mismatch{_sup}"),
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
        result["charts"] = _clean_charts(raw.get("charts"), result["facts"])
        hits = sum(1 for f in result["facts"] if f["verified_in_source"])
        ms = int((_time.perf_counter() - t0) * 1000)
        yield {"stage": "verify-citations", "status": "ok",
               "detail": f"{hits}/{len(result['facts'])} quote(s) verbatim in source",
               "elapsed_ms": ms, "result": None}

    # ── analyse-patterns ───────────────────────────────────────────────
    # Runs AFTER citations: the model proposed patterns in the extract
    # call, but they are recomputed here against the already-verified,
    # already-cited facts and only then surfaced. Nothing is released.
    if failed:
        yield {"stage": "analyse-patterns", "status": "skip", "detail": "",
               "elapsed_ms": None, "result": None}
    else:
        ev = _stage("analyse-patterns")
        yield ev
        t0 = _time.perf_counter()
        _raw_pats = [p for p in (raw.get("patterns") or [])
                     if isinstance(p, dict)]
        result["patterns"] = verify_patterns(result["facts"],
                                             raw.get("patterns"))
        pats = result["patterns"]
        p_ok = sum(1 for p in pats if p.get("passed"))
        p_unver = sum(1 for p in pats if p.get("error"))
        _pats_dropped = len(_raw_pats) - len(pats)
        # Rebuild insights now that verified patterns exist.
        result["insights"] = build_insights(
            result["facts"], result["checks"], result["risks"],
            result["summary"], result["patterns"]
        )
        ms = int((_time.perf_counter() - t0) * 1000)
        _psup = (f"; suppressed {_pats_dropped} uncited pattern(s)"
                 if _pats_dropped > 0 else "")
        yield {"stage": "analyse-patterns", "status": "ok",
               "detail": (f"{len(pats)} pattern(s): {p_ok} confirmed, "
                          f"{p_unver} unverifiable{_psup}"),
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
        "insights": "",
        "charts": [],
        "risks": [],
        "patterns": [],
        "facts": [],
        "checks": [],
        "redaction_count": 0,
        "redacted_preview": "",
        "fallback_used": False,
        "provider": "",
        "summary": {"facts_extracted": 0, "checks_run": 0,
                    "checks_passed": 0, "checks_failed": 0},
        "error": None,
    }


def _clean_charts(charts, facts) -> list[dict]:
    """The model may pick ANY kind — creativity is welcome — but every
    point must resolve to a known, numeric, quote-pinned fact. Points
    that don't are dropped; a chart with no surviving points is dropped.
    Kind is passed through untouched: the UI's deterministic registry
    decides whether it renders or degrades to the data table."""
    by_id = {str(f.get("id")): f for f in facts or []
             if isinstance(f, dict)}
    out = []
    for c in charts or []:
        if not isinstance(c, dict):
            continue
        points = []
        ok = True
        for p in c.get("points") or []:
            if not isinstance(p, dict):
                ok = False
                break
            f = by_id.get(str(p.get("fact_id")))
            if (not f or f.get("value") is None
                    or not f.get("verified_in_source")):
                ok = False
                break
            points.append({"fact_id": str(p["fact_id"]),
                           "label": str(p.get("label") or f.get("claim", "")),
                           "value": float(f["value"])})
        if ok and points:
            out.append({"kind": str(c.get("kind", "")).strip().lower(),
                        "title": str(c.get("title", "")).strip(),
                        "points": points})
    return out


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


def _fmt_num(n) -> str:
    """Human number for insight prose: no decimals when whole."""
    if n is None:
        return "n/a"
    if isinstance(n, float) and n.is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}"


# Only in-token separators (commas, decimal points) join a single number;
# a space starts a new token, so distinct space-separated numbers each mask
# to their own '#' and intervening words are preserved.
_DIGIT_RUN_RE = re.compile(r"\d[\d,.]*\d|\d")


def _safe_desc(text) -> str:
    """A model-authored description is UNVERIFIED prose, so before it can
    appear inside the verified-insights block every numeric run in it is
    masked to '#'. This is what makes the block's promise literal: the
    only real numbers it can show are the ones build_insights computed
    itself (expected/actual/gap/counts), never a figure the model typed
    into its description text."""
    return _DIGIT_RUN_RE.sub("#", str(text or "").strip())


def build_insights(facts: list, checks: list, risks: list,
                   summary: dict, patterns: list | None = None) -> str:
    """Compose a concise, plain-language insights summary from data the
    pipeline has ALREADY verified — never from raw model text. Every
    number in the output is one build_insights computed from verified
    values; every description echoed from the model is run through
    _safe_desc first, which masks any digits the model wrote. This is why
    the summary inherits the 'no unverified number' guarantee: no figure
    the pipeline did not re-check can appear.

    Trend vs exception classification keys on the check's `operation`
    (a structural fact the pipeline recorded), not on the model's prose.

    Returns a short multi-line string (one insight per line), or "" when
    there is nothing verified to report."""
    lines: list[str] = []

    passed = [c for c in (checks or [])
              if c.get("passed") and not c.get("error")]
    failed = [c for c in (checks or [])
              if not c.get("passed") and not c.get("error")]
    unverifiable = [c for c in (checks or []) if c.get("error")]

    # 1. Trends first — a percent_change check that verified is a real,
    #    checked trend. Classified on operation, not description text.
    for c in passed:
        actual = c.get("actual")
        if c.get("operation") == "percent_change" and actual is not None:
            direction = "up" if actual > 0 else "down" if actual < 0 else "flat"
            lines.append(
                f"Trend verified ({_safe_desc(c.get('description'))}): "
                f"{direction} {_fmt_num(abs(actual))}%, independently "
                f"recomputed."
            )

    # 2. Exceptions — every failed arithmetic check is a confirmed
    #    discrepancy. The numeric clause only appears when BOTH figures
    #    are present, so a partial row degrades to just its description.
    for c in failed:
        exp, act = c.get("expected"), c.get("actual")
        desc = _safe_desc(c.get("description")) or "a calculation"
        if exp is not None and act is not None:
            gap = act - exp
            lines.append(
                f"Exception: {desc} did not reconcile — document states "
                f"{_fmt_num(exp)}, the numbers add to {_fmt_num(act)} "
                f"(off by {_fmt_num(abs(gap))})."
            )
        else:
            lines.append(f"Exception: {desc} did not reconcile.")

    # 2b. Patterns — composition/ratio/sign relationships the model spotted
    #     and Python recomputed. Only confirmed patterns state their
    #     Python-computed figure; unverifiable ones are flagged as such.
    #     Descriptions are digit-masked like everything model-authored.
    _kind_word = {"composition": "share", "ratio": "ratio", "sign": "flag",
                  "threshold": "flag"}
    for p in patterns or []:
        desc = _safe_desc(p.get("description")) or "a pattern"
        if p.get("error"):
            lines.append(f"Pattern (unverifiable): {desc} — could not be "
                         f"recomputed ({p.get('error')}).")
        elif p.get("passed"):
            act = p.get("actual")
            kind = p.get("kind", "")
            if kind in ("composition", "ratio") and act is not None:
                lines.append(f"Pattern verified ({desc}): "
                             f"{_fmt_num(act)}% — recomputed in Python.")
            elif kind in ("sign", "threshold") and act is not None:
                # A confirmed sign/threshold pattern means the flagged
                # ADVERSE condition actually holds — frame it as a flag,
                # not a reassuring "verified", so the reader reads it as
                # the warning it is.
                lines.append(f"Flag confirmed ({desc}): value is "
                             f"{_fmt_num(act)} — the flagged condition "
                             f"holds in the data.")
            else:
                lines.append(f"Pattern verified: {desc}.")

    # 3. Evidenced risks, ordered by severity so the reader sees the
    #    worst first. These already passed the evidence-citation filter;
    #    their prose is masked of digits like every other description.
    order = {"high": 0, "medium": 1, "low": 2}
    for r in sorted(risks or [], key=lambda x: order.get(x.get("severity"), 1)):
        desc = _safe_desc(r.get("description"))
        if desc:
            lines.append(f"Risk ({r.get('severity','medium')}): {desc}")

    # 4. A one-line coverage note so the reader knows the scope of what
    #    was checked — transparency about how much was actually verified.
    s = summary or {}
    checked = s.get("checks_run", 0)
    if checked:
        clean = not failed and not unverifiable
        verdict = ("all figures verified cleanly" if clean
                   else f"{len(failed)} mismatch(es), "
                        f"{len(unverifiable)} unverifiable")
        lines.append(
            f"Coverage: {s.get('facts_extracted',0)} figure(s) extracted, "
            f"{checked} calculation(s) re-checked in Python — {verdict}."
        )

    return "\n".join(lines)


def analyze(file_path: str, question: str,
            provider: str | None = None,
            api_key: str | None = None) -> dict:
    """Full pipeline. Never raises — errors come back in the 'error' field.
    Thin consumer of analyze_stream(): one code path, two presentations."""
    result = None
    for event in analyze_stream(file_path, question,
                                provider=provider, api_key=api_key):
        if event["stage"] == "release":
            result = event["result"]
    return result
