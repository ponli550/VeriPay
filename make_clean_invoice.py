"""Generates clean_invoice.pdf — the paid-leg demo document.

Unlike sample_report.pdf, nothing here is wrong: the three service line
items genuinely sum to the stated total, and the stated growth
percentage is exactly consistent with the prior-period figure. This is
the document scripts/devnet_live.py runs a REAL analyze() call against
(no DEMO_FALLBACK) so the paid leg is upload -> verify -> pay end to
end, against a document that can actually earn a clean bill of health.

It carries the same categories of PII as make_sample.py (name, email,
phone, IC) so redaction still has real work to do.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def build_pdf(path: str = "clean_invoice.pdf") -> None:
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    y = height - 30 * mm

    def line(text, size=11, gap=7 * mm, bold=False):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(25 * mm, y, text)
        y -= gap

    line("Meridian Consulting Sdn Bhd", size=16, bold=True, gap=10 * mm)
    line("Invoice Number: INV-2025-0417", size=12, gap=10 * mm)

    line("Prepared by Siti Binti Hassan", gap=6 * mm)
    line("Contact: siti.hassan@meridian.com.my", gap=6 * mm)
    line("Phone: 019-8765432", gap=6 * mm)
    line("IC: 900101-14-5678", gap=10 * mm)

    line("Services Rendered", size=13, bold=True, gap=8 * mm)
    line("Strategic advisory services 45,000", gap=6 * mm)
    line("Financial audit services 32,500", gap=6 * mm)
    line("Technology consulting services 22,500", gap=6 * mm)
    line("Total amount due 100,000", gap=10 * mm)

    c.showPage()

    # Page 2 — prior-period comparison, so a trend exists to verify.
    y = height - 30 * mm
    line("Prior Period Comparison (Q1 2025)", size=13, bold=True, gap=10 * mm)
    line("Total amount due prior quarter 80,000", gap=6 * mm)
    line("Growth vs prior quarter: 25.0%", gap=6 * mm)

    c.showPage()
    c.save()


if __name__ == "__main__":
    build_pdf()
    print("Wrote clean_invoice.pdf")
