"""Generate the synthetic payslip PDFs used by the tests (fictional people and numbers).

    python backend/tests/fixtures/make_payslips.py

Needs reportlab (dev only) and a TrueType font with Hebrew (Arial on Windows, or pass a
path). The PDFs are committed, so the tests themselves do not need reportlab.

payslip_e004.pdf     - Hebrew stored in *visual* order, as many payroll systems emit it;
                       the pay data of sample employee E-004 (see the Excel sheet).
payslip_logical.pdf  - logical order; commission and bonus on separate lines, pension
                       given only as an amount (no %), plus an employer-pension line.
payslip_scanned.pdf  - no text layer at all (like a scan / photo).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
FONT = sys.argv[1] if len(sys.argv) > 1 else r"C:\Windows\Fonts\arial.ttf"

PRIVATE = [  # must never reach the language model
    "שם העובד: ישראל ישראלי",
    "מספר זהות: 123456782",
    "כתובת: רחוב הרצל 10 תל אביב",
    "טלפון: 050-1234567",
    "בנק 12 סניף 600 חשבון 7654321",
]

E004 = [
    "תלוש שכר לחודש 09/2026 - חברה לדוגמה בע\"מ",
    *PRIVATE,
    "תפקיד: מנהל אזור מכירות",
    "תשלומים",
    "שכר בסיס 16,000.00",
    "עמלות 3,000.00",
    "שווי רכב 4,790.00",
    "ניכויים",
    "מס הכנסה 3,991.00",
    "ביטוח לאומי ומס בריאות 2,301.00",
    "קופת גמל - חלק עובד 6.00% 1,140.00",
    "קרן השתלמות - חלק עובד 2.50% 400.00",
    "השתתפות עובד ברכב 400.00",
    "נתונים נוספים",
    "נקודות זיכוי 2.25",
    "נטו לתשלום 10,693.00",
]

LOGICAL = [
    "תלוש משכורת 08/2026",
    *PRIVATE,
    "שכר בסיס 14,200.00",
    "עמלות מכירה 2,000.00",
    "בונוס רבעוני 1,000.00",
    "תגמולי עובד לפנסיה 852.00",
    "תגמולי מעביד לפנסיה 6.50% 923.00",
    "קרן השתלמות עובד 2.5% 355.00",
    "נקודות זיכוי 2.75",
]


def visual(line: str) -> str:
    """Logical -> visual order (what an RTL-unaware PDF writer stores)."""
    return re.sub(r"[0-9A-Za-z][0-9A-Za-z.,%/:\-]*", lambda m: m.group(0)[::-1], line[::-1])


def write(path: Path, lines: list[str], order=lambda s: s) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Heb", 12)
    y = 800
    for line in lines:
        c.drawRightString(560, y, order(line))
        y -= 22
    c.save()


def main() -> None:
    pdfmetrics.registerFont(TTFont("Heb", FONT))
    write(HERE / "payslip_e004.pdf", E004, visual)
    write(HERE / "payslip_logical.pdf", LOGICAL)
    c = canvas.Canvas(str(HERE / "payslip_scanned.pdf"), pagesize=A4)
    c.rect(50, 50, 500, 700)  # a "scan": shapes only, no text layer
    c.save()
    print("written to", HERE)


if __name__ == "__main__":
    main()
