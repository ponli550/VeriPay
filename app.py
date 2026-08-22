"""FinVerify UI — Member 2 owns this file.

Set USE_FAKE = True to develop without an API key or backend dependency.
Flip to False once backend.py is wired up and tested.
"""

import gradio as gr

USE_FAKE = False

FAKE = {
    "answer": (
        "Total revenue was stated as RM 2,750,000 across three segments. "
        "However, the individual segments (Product RM 1,200,000 + Services "
        "RM 1,150,000 + Licensing RM 300,000) sum to only RM 2,650,000 — "
        "a discrepancy of RM 100,000."
    ),
    "facts": [
        {"id": "f1", "claim": "Product revenue", "value": 1200000.0,
         "page": "Page 1", "quote": "Product revenue 1,200,000",
         "verified_in_source": True},
        {"id": "f2", "claim": "Services revenue", "value": 1150000.0,
         "page": "Page 1", "quote": "Services revenue 1,150,000",
         "verified_in_source": True},
        {"id": "f3", "claim": "Licensing revenue", "value": 300000.0,
         "page": "Page 1", "quote": "Licensing revenue 300,000",
         "verified_in_source": True},
        {"id": "f4", "claim": "Total revenue (stated)", "value": 2750000.0,
         "page": "Page 1", "quote": "Total revenue 2,750,000",
         "verified_in_source": True},
    ],
    "checks": [
        {"description": "Stated total vs sum of segments",
         "expected": 2750000.0, "actual": 2650000.0,
         "passed": False, "error": None},
    ],
    "summary": {"facts_extracted": 4, "checks_run": 1,
                "checks_passed": 0, "checks_failed": 1},
    "redaction_count": 4,
    "redacted_preview": "--- Page 1 ---\nPrepared by [NAME_REDACTED] ...",
    "recommendation": ("Reconcile the RM 100,000 gap between stated total "
                       "revenue and the sum of its segments before sign-off."),
    "fallback_used": False,
    "error": None,
}

# ── Styling ────────────────────────────────────────────────────────────────

CUSTOM_CSS = """
.mismatch-card {
    background: #FEE2E2; border-left: 4px solid #DC2626;
    padding: 14px 18px; border-radius: 8px; margin: 10px 0;
    color: #111827;
}
.verified-card {
    background: #DCFCE7; border-left: 4px solid #16A34A;
    padding: 14px 18px; border-radius: 8px; margin: 10px 0;
    color: #111827;
}
.warning-card {
    background: #FEF3C7; border-left: 4px solid #D97706;
    padding: 14px 18px; border-radius: 8px; margin: 10px 0;
    color: #111827;
}
.cached-banner {
    background: #FEF3C7; border: 1px solid #D97706;
    padding: 10px 16px; border-radius: 8px; margin: 6px 0 12px 0;
    color: #92400E; font-weight: 600; font-size: 14px;
}
.stat-bar {
    display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px;
}
.stat-box {
    padding: 8px 16px; border-radius: 8px; font-size: 14px;
    font-weight: 600;
}
.stat-facts { background: #EFF6FF; color: #1E40AF; }
.stat-pass  { background: #DCFCE7; color: #166534; }
.stat-fail  { background: #FEE2E2; color: #991B1B; }
.stat-pii   { background: #FEF3C7; color: #92400E; }
.pipe-panel {
    background: #0F172A; border-radius: 8px; padding: 12px 16px;
    margin-bottom: 12px; font-family: ui-monospace, Menlo, monospace;
    font-size: 13px; color: #E2E8F0;
}
.pipe-row { display: flex; align-items: center; gap: 10px; padding: 3px 0; }
.pipe-ok   { color: #34D399; }
.pipe-fail { color: #F87171; }
.pipe-run  { color: #38BDF8; }
.pipe-wait { color: #475569; }
.pipe-skip { color: #64748B; text-decoration: line-through; }
.pipe-ms   { margin-left: auto; color: #64748B; font-size: 11px; }
.rec-card {
    background: #EFF6FF; border-left: 4px solid #2563EB;
    padding: 14px 18px; border-radius: 8px; margin: 10px 0;
    color: #111827;
}
.source-card {
    background: #F8FAFC; border: 1px solid #E2E8F0;
    border-radius: 8px; padding: 12px 16px; margin: 8px 0;
    color: #111827;
}
.source-card blockquote {
    border-left: 3px solid #94A3B8; margin: 6px 0 0 0;
    padding-left: 10px; color: #475569; font-style: italic;
}
.page-tag {
    display: inline-block; background: #E2E8F0; color: #334155;
    padding: 2px 8px; border-radius: 4px; font-size: 12px;
    font-weight: 600; margin-left: 6px;
}
.redacted-pre {
    background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px;
    padding: 12px; font-family: ui-monospace, Menlo, monospace;
    font-size: 12px; color: #334155; white-space: pre-wrap;
    max-height: 260px; overflow-y: auto;
}
"""

# ── Renderers ──────────────────────────────────────────────────────────────

def _fmt(n):
    if n is None:
        return "n/a"
    if isinstance(n, float) and n.is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}"


def render_summary(result: dict) -> str:
    s = result.get("summary", {})
    r = result.get("redaction_count", 0)
    banner = ""
    if result.get("fallback_used"):
        banner = (
            '<div class="cached-banner">⚠ CACHED — the live model call '
            "failed and the demo fallback answered instead. Verification "
            "below still ran for real against these facts.</div>"
        )
    return f"""{banner}<div class="stat-bar">
  <div class="stat-box stat-facts">📊 {s.get('facts_extracted',0)} facts extracted</div>
  <div class="stat-box stat-pass">✅ {s.get('checks_passed',0)} verified</div>
  <div class="stat-box stat-fail">❌ {s.get('checks_failed',0)} mismatches</div>
  <div class="stat-box stat-pii">🔒 {r} PII items redacted</div>
</div>"""


def render_checks(result: dict) -> str:
    checks = result.get("checks") or []
    html = ""

    rec = (result.get("recommendation") or "").strip()
    if rec:
        html += (f'<div class="rec-card"><strong>💡 Recommendation:</strong> '
                 f'{rec}</div>')

    if not checks:
        html += ('<div class="warning-card">'
                 "No arithmetic claims to verify.</div>")
        return html

    for c in checks:
        if c.get("error"):
            html += (f'<div class="warning-card">'
                     f'<strong>⚠️ Could not verify:</strong> '
                     f'{c.get("description","")}<br>'
                     f'{c["error"]}</div>')
        elif c.get("passed"):
            html += (f'<div class="verified-card">'
                     f'<strong>✅ Verified:</strong> '
                     f'{c.get("description","")}<br>'
                     f'AI stated: <code>{_fmt(c.get("expected"))}</code> · '
                     f'Computed: <code>{_fmt(c.get("actual"))}</code>'
                     f'</div>')
        else:
            diff = None
            if c.get("expected") is not None and c.get("actual") is not None:
                diff = c["actual"] - c["expected"]
            html += (f'<div class="mismatch-card">'
                     f'<strong>❌ MISMATCH:</strong> '
                     f'{c.get("description","")}<br>'
                     f'AI stated: <code>{_fmt(c.get("expected"))}</code> · '
                     f'Computed: <code>{_fmt(c.get("actual"))}</code> · '
                     f'Off by: <code>{_fmt(diff)}</code>'
                     f'</div>')

    return html


def render_facts(result: dict) -> str:
    facts = result.get("facts") or []
    if not facts:
        return "<p>No facts extracted.</p>"
    html = ""
    for f in facts:
        q = f.get("quote") or "(no quote)"
        flag = "" if f.get("verified_in_source", True) else (
            '<div style="color:#B45309;font-size:12px;margin-top:4px;">'
            '⚠️ quote not found verbatim in source</div>'
        )
        html += (f'<div class="source-card">'
                 f'<strong>{f.get("claim","")}</strong> — '
                 f'<code>{_fmt(f.get("value"))}</code>'
                 f'<span class="page-tag">{f.get("page","?")}</span>'
                 f'<blockquote>{q}</blockquote>{flag}</div>')
    return html


def render_redacted(result: dict) -> str:
    preview = result.get("redacted_preview") or ""
    if not preview:
        return ""
    import html as _html
    return (f'<div class="redacted-pre">{_html.escape(preview)}</div>'
            '<p style="font-size:12px;color:#64748B;">This is the start of '
            'the exact text sent to the model — PII already replaced by '
            '<code>[..._REDACTED]</code> tokens. Known limit: a bare name '
            'with no title, patronymic, or signature cue is not caught, '
            'and scanned-image PII is invisible to text extraction.</p>')


# ── Pipeline (in-product CI) ──────────────────────────────────────────────

STAGE_LABELS = [
    ("parse", "Parse document"),
    ("redact", "Redact PII"),
    ("extract", "Extract facts (model)"),
    ("verify-math", "Verify arithmetic"),
    ("verify-citations", "Verify citations"),
    ("release", "Release to you"),
]

_ICON = {"ok": ("✓", "pipe-ok"), "fail": ("✗", "pipe-fail"),
         "running": ("●", "pipe-run"), "skip": ("⊘", "pipe-skip"),
         "pending": ("○", "pipe-wait")}


def render_pipeline(states: dict) -> str:
    rows = []
    for key, label in STAGE_LABELS:
        st = states.get(key, {"status": "pending", "detail": "", "elapsed_ms": None})
        icon, cls = _ICON.get(st["status"], _ICON["pending"])
        detail = f' — {st["detail"]}' if st.get("detail") else ""
        ms = (f'<span class="pipe-ms">{st["elapsed_ms"]} ms</span>'
              if st.get("elapsed_ms") is not None else "")
        rows.append(f'<div class="pipe-row"><span class="{cls}">{icon}</span>'
                    f'<span>{label}{detail}</span>{ms}</div>')
    return '<div class="pipe-panel">' + "".join(rows) + "</div>"


# ── Gradio handlers ───────────────────────────────────────────────────────

def run(file_path, question):
    """Generator: the user watches the CI pipeline run; the answer and
    every card render only after the release stage — nothing reaches the
    screen unverified."""
    if USE_FAKE:
        states = {k: {"status": "ok", "detail": "", "elapsed_ms": 0}
                  for k, _ in STAGE_LABELS}
        yield ("", "", "", "", "", render_pipeline(states))
        yield (FAKE["answer"], render_summary(FAKE), render_checks(FAKE),
               render_facts(FAKE), render_redacted(FAKE),
               render_pipeline(states))
        return

    import backend
    states = {}
    blank = ("", "", "", "", "")
    yield (*blank, render_pipeline(states))
    result = None
    for ev in backend.analyze_stream(file_path, question):
        states[ev["stage"]] = {"status": ev["status"],
                               "detail": ev.get("detail", ""),
                               "elapsed_ms": ev.get("elapsed_ms")}
        if ev["stage"] == "release":
            result = ev["result"]
        yield (*blank, render_pipeline(states))

    # Errors route to an HTML slot — a Textbox would render the markup
    # as literal angle-bracket soup.
    if result.get("error"):
        error_html = f'<div class="warning-card">⚠️ {result["error"]}</div>'
        yield ("", error_html, "", "", "", render_pipeline(states))
        return

    yield (
        result.get("answer", ""),
        render_summary(result),
        render_checks(result),
        render_facts(result),
        render_redacted(result),
        render_pipeline(states),
    )


def clear(file_path):
    """Wipe the session. Deletes the uploaded temp file from disk (only
    if it lives in Gradio's temp dir) — this is what makes the retention
    promise in the notice below true, not aspirational."""
    import backend
    backend.purge_upload(file_path)
    return None, "", "", "", "", "", "", ""


# ── App layout ─────────────────────────────────────────────────────────────

with gr.Blocks(title="FinVerify — AI Financial Report Verifier") as demo:

    gr.Markdown(
        "# 🔍 FinVerify\n"
        "AI-powered financial analysis with independent verification. "
        "Every number cited to its source. Every calculation re-checked "
        "in Python."
    )

    with gr.Row():
        # ── Left: input ───────────────────────────────────────────────
        with gr.Column(scale=1, min_width=280):
            file_in = gr.File(
                label="Upload report (PDF or Excel)",
                file_types=[".pdf", ".xlsx"],
                type="filepath",
            )
            question_in = gr.Textbox(
                label="Your question",
                value="What was total revenue, and does it add up?",
                lines=2,
            )
            go = gr.Button("🔍 Analyse", variant="primary", size="lg")
            clear_btn = gr.Button("🗑️ Clear session", size="sm")
            gr.Markdown(
                "_PII is redacted locally before any text reaches the AI — "
                "only redacted text is sent, never the file. The upload is "
                "held as a temporary file for this session only; **Clear "
                "session** deletes it from disk and wipes all results._"
            )
            with gr.Accordion("🔎 What left this machine", open=False):
                redacted_out = gr.HTML()

        # ── Centre: answer + verification ─────────────────────────────
        with gr.Column(scale=2, min_width=400):
            gr.Markdown("### 🛠 Verification pipeline")
            pipeline_out = gr.HTML()
            answer_out = gr.Textbox(label="Analysis", lines=5, interactive=False)
            summary_out = gr.HTML()
            checks_out = gr.HTML()

        # ── Right: source citations ───────────────────────────────────
        with gr.Column(scale=1, min_width=280):
            gr.Markdown("### 📄 Sources")
            facts_out = gr.HTML()

    # Wire buttons
    go.click(
        run, [file_in, question_in],
        [answer_out, summary_out, checks_out, facts_out, redacted_out,
         pipeline_out],
    )
    clear_btn.click(
        clear, [file_in],
        [file_in, question_in, answer_out, summary_out, checks_out,
         facts_out, redacted_out, pipeline_out],
    )


if __name__ == "__main__":
    demo.launch(
        theme=gr.themes.Soft(
            primary_hue="blue",
            font=gr.themes.GoogleFont("Inter"),
        ),
        css=CUSTOM_CSS,
    )
