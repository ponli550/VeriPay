"""Generates sample_report.pdf — the demo document.

Contains a deliberate RM 100,000 revenue discrepancy (for the
caught-failure demo), a correctly-summing expenses table (for the
clean-pass demo), and PII (for the redaction demo).

The exact line text matters: it must match the quotes in
fixtures/cached_response.json character for character, since both the
live LLM path and the fallback path cite these lines.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def build_pdf(path: str = "sample_report.pdf") -> None:
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    y = height - 30 * mm

    def line(text, size=11, gap=7 * mm, bold=False):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(25 * mm, y, text)
        y -= gap

    line("Nusantara Holdings Berhad", size=16, bold=True, gap=10 * mm)
    line("Quarterly Financial Report — Q3 2025", size=12, gap=10 * mm)

    line("Prepared by Ahmad Bin Ali", gap=6 * mm)
    line("Contact: ahmad.ali@nusantara.com.my", gap=6 * mm)
    line("Phone: 012-3456789", gap=6 * mm)
    line("IC: 880512-14-5533", gap=10 * mm)

    line("Revenue", size=13, bold=True, gap=8 * mm)
    line("Product revenue 1,200,000", gap=6 * mm)
    line("Services revenue 1,150,000", gap=6 * mm)
    line("Licensing revenue 300,000", gap=6 * mm)
    line("Total revenue 2,750,000", gap=10 * mm)

    line("Operating Expenses", size=13, bold=True, gap=8 * mm)
    line("Salaries and wages 850,000", gap=6 * mm)
    line("Marketing and advertising 420,000", gap=6 * mm)
    line("Administrative expenses 180,000", gap=6 * mm)
    line("Total operating expenses 1,450,000", gap=10 * mm)

    c.showPage()

    # Page 2 — prior-quarter comparison, so a trend exists to verify.
    y = height - 30 * mm
    line("Prior Quarter Comparison (Q2 2025)", size=13, bold=True, gap=10 * mm)
    line("Total revenue prior quarter 2,500,000", gap=6 * mm)
    line("Revenue growth vs prior quarter: 10.0%", gap=6 * mm)

    c.showPage()
    c.save()


if __name__ == "__main__":
    build_pdf()
    print("Wrote sample_report.pdf")
