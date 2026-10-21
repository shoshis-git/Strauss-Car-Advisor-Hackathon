"""Payslip upload: digital PDF -> local text -> redaction -> field extraction ->
deterministic verification.

Privacy: the file is read in memory and never stored; identifying details (ID number,
bank account, address, phone, e-mail, name lines) are masked locally *before* any text
reaches the language model. Only values the employee then confirms enter the state.

Trust: the model only points at values - every number it returns must literally appear
in the payslip text (on the line it quotes), otherwise it is dropped. Sums (commission +
bonus) are computed here, never by the model."""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..data_loader.config_loader import AppConfig
from .llm import LLMClient
from .state import FIELD_LABELS, ConversationState

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 4
MIN_TEXT_CHARS = 40
REDACTED = "[הושחר]"

# fields a payslip can fill, with sane ranges (out of range = misread -> dropped)
PAYSLIP_FIELDS: dict[str, tuple[float, float]] = {
    "base_salary": (1_000, 300_000),
    "commissions_bonuses": (0, 300_000),
    "pension_pct": (0, 30),
    "hishtalmut_pct": (0, 30),
    "credit_points": (0, 20),
    "current_participation": (0, 20_000),
}


class PayslipError(ValueError):
    """A problem with the uploaded file - message in Hebrew, shown to the employee."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# 1. PDF -> text (local)
# --------------------------------------------------------------------------- #
_HEB_WORDS = ("שכר", "נטו", "ברוטו", "ניכויים", "תשלומים", "פנסיה", "השתלמות", "זיכוי",
              "הכנסה", "עובד", "בסיס", "לאומי", "תלוש")
_LTR_RUN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.,%/:\-]*")


def normalize_rtl(text: str) -> str:
    """Many payroll PDFs store Hebrew in visual order ('רכש' comes out as 'שכר' reversed).
    Detect it from common payslip words and flip each line back, keeping numbers intact."""
    forward = sum(text.count(w) for w in _HEB_WORDS)
    backward = sum(text.count(w[::-1]) for w in _HEB_WORDS)
    if backward <= forward:
        return text
    return "\n".join(_LTR_RUN.sub(lambda m: m.group(0)[::-1], line[::-1])
                     for line in text.splitlines())


def read_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if len(data) > MAX_BYTES:
        raise PayslipError(f"הקובץ גדול מדי (מעל {MAX_BYTES // (1024 * 1024)}MB)", 413)
    if not data.startswith(b"%PDF-"):
        raise PayslipError("אפשר להעלות רק תלוש שכר בפורמט PDF", 415)
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PayslipError("הקובץ מוגן בסיסמה. הורד/י את התלוש ללא סיסמה ונסה/י שוב.")
        if len(reader.pages) > MAX_PAGES:
            raise PayslipError(f"הקובץ ארוך מדי לתלוש שכר (מעל {MAX_PAGES} עמודים)")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PayslipError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise PayslipError("לא הצלחתי לקרוא את הקובץ - נראה שהוא פגום") from None
    if len(re.findall(r"[\w]", text)) < MIN_TEXT_CHARS:
        raise PayslipError("לא נמצא טקסט בקובץ - נראה שזה צילום או סריקה. יש להעלות את "
                           "התלוש הדיגיטלי (PDF ממערכת השכר), או למסור את הנתונים בצ'אט.")
    return normalize_rtl(text)


# --------------------------------------------------------------------------- #
# 2. Redaction (local, before anything leaves the server)
# --------------------------------------------------------------------------- #
# bounded by "not a letter" (not \b): extraction often glues digits to labels ('1234567טלפון')
_SENSITIVE_LABEL = re.compile(
    r"(?<![^\W\d_])(ת\s?[\"״.]\s?ז|תעודת\s*זהות|מספר\s*זהות|מס['׳]?\s*זהות|זהות|דרכון|כתובת|רחוב|"
    r"רח['׳]|מיקוד|טלפון|נייד|פקס|דוא\"?ל|מייל|e-?mail|בנק|סניף|חשבון|שם\s*העובד|שם\s*עובד|"
    r"שם\s*משפחה|שם\s*פרטי|מספר\s*עובד)(?![^\W\d_])", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"(?<![\d.,])\d(?:[\d-]*\d){6,}(?![\d.,])")  # ID / account / phone
_EMAIL = re.compile(r"\S+@\S+")


def redact(text: str) -> str:
    """Mask identifying details. A line with an identifying label is masked whole: after
    PDF extraction the value may sit before or after its label, so a partial mask leaks."""
    out = []
    for line in text.splitlines():
        if _SENSITIVE_LABEL.search(line):
            out.append(REDACTED)
            continue
        line = _EMAIL.sub(REDACTED, line)
        out.append(_LONG_DIGITS.sub(
            lambda m: REDACTED if len(re.sub(r"\D", "", m.group(0))) >= 7 else m.group(0), line))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 3. Extraction - the model (or the deterministic brain) points at values
# --------------------------------------------------------------------------- #
EXTRACT_TOOL = {
    "name": "report_payslip_fields",
    "description": "דיווח הערכים שמופיעים בתלוש השכר, כפי שהם כתובים בו.",
    "input_schema": {
        "type": "object",
        "properties": {
            "occupation": {"type": "string", "description": "התפקיד / העיסוק כפי שכתוב בתלוש"},
            "base_salary": {"type": "number", "description": "שכר בסיס חודשי"},
            "commission_items": {
                "type": "array", "items": {"type": "number"},
                "description": "סכום כל שורת עמלות / בונוס / פרמיה בנפרד, כפי שכתוב (בלי לסכם)"},
            "pension_pct": {"type": "number",
                            "description": "אחוז הפרשת העובד לפנסיה/קופת גמל - רק אם האחוז כתוב"},
            "hishtalmut_pct": {"type": "number",
                               "description": "אחוז הפרשת העובד לקרן השתלמות - רק אם כתוב"},
            "credit_points": {"type": "number", "description": "נקודות זיכוי"},
            "car_participation": {"type": "number",
                                  "description": "ניכוי השתתפות העובד ברכב (לא שווי השימוש)"},
            "evidence": {"type": "object",
                         "description": "לכל שדה שדווח: השורה מהתלוש שממנה נלקח, מועתקת כלשונה",
                         "additionalProperties": {"type": "string"}},
        },
    },
}

EXTRACT_SYSTEM = """\
אתה קורא תלושי שכר ישראליים. קיבלת את הטקסט של תלוש (פרטים מזהים הושחרו).
הפעל את הכלי report_payslip_fields פעם אחת עם הערכים שמופיעים בתלוש.
- העתק מספרים בדיוק כפי שהם כתובים. לעולם אל תחשב, אל תסכם ואל תעגל.
- אחוזי הפרשה: רק חלק העובד, ורק אם האחוז עצמו כתוב. אם כתוב רק סכום - השמט את השדה.
- השתתפות ברכב: הניכוי מהעובד (השתתפות/ניכוי רכב), לא "שווי רכב"/"שווי שימוש".
- שדה שלא מופיע - השמט. ב-evidence העתק לכל שדה את השורה המקורית.
"""


def _num(s: str) -> float:
    return float(s.replace(",", ""))


_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


# a percent sign glued to its number first ('6.00%' / '%6.00' after RTL fixing), and only
# then with a space - so '1,140.00 %6.00' reads 6.00, not 140
_PCT_PATTERNS = [r"(?<![\d,.])(\d+(?:\.\d+)?)%", r"%(\d+(?:\.\d+)?)",
                 r"(?<![\d,.])(\d+(?:\.\d+)?)\s%", r"%\s(\d+(?:\.\d+)?)"]


def _first_number(line: str, pct: bool = False) -> Optional[float]:
    if pct:
        for pat in _PCT_PATTERNS:
            if m := re.search(pat, line):
                return _num(m.group(1))
        return None
    m = _NUMBER.search(line)
    return _num(m.group(0)) if m else None


def rule_based_extract(text: str) -> dict[str, Any]:
    """Deterministic label-based extraction (tests / LLM_PROVIDER=rules)."""
    out: dict[str, Any] = {"evidence": {}}
    items: list[float] = []
    for line in text.splitlines():
        if "occupation" not in out and (m := re.search(r"(?:תפקיד|עיסוק)\s*:\s*(.+)", line)):
            out["occupation"], out["evidence"]["occupation"] = m.group(1).strip(), line.strip()
            continue
        employer = re.search(r"מעביד|מעסיק", line)
        checks = [
            ("base_salary", re.search(r"שכר\s*(?:ה)?בסיס|משכורת\s*בסיס", line), False),
            ("credit_points", re.search(r"נקודות\s*זיכוי|נק['׳]?\s*זיכוי", line), False),
            ("hishtalmut_pct", re.search(r"השתלמות", line) and not employer, True),
            ("pension_pct", re.search(r"פנסיה|גמל|תגמולי", line) and not employer, True),
            ("car_participation", re.search(r"השתתפות", line) and re.search(r"רכב", line), False),
        ]
        for key, hit, pct in checks:
            if hit and key not in out:
                value = _first_number(line, pct)
                if value is not None:
                    out[key], out["evidence"][key] = value, line.strip()
                break
        if re.search(r"עמלות|עמלה|בונוס|פרמיה", line):
            value = _first_number(line)
            if value is not None:
                items.append(value)
                out["evidence"].setdefault("commission_items", line.strip())
    if items:
        out["commission_items"] = items
    return out


def llm_extract(text: str, llm: LLMClient) -> dict[str, Any]:
    from .rule_based import RuleBasedLLM

    if isinstance(llm, RuleBasedLLM):
        return rule_based_extract(text)
    step = llm.step(system=EXTRACT_SYSTEM, tools=[EXTRACT_TOOL], state=ConversationState(),
                    messages=[{"role": "user", "content": "טקסט התלוש:\n" + text}])
    call = next((c for c in step.tool_calls if c.name == EXTRACT_TOOL["name"]), None)
    if call is not None:
        return call.input
    try:  # some models answer with the JSON as text
        return json.loads(re.search(r"\{.*\}", step.text or "", re.S).group(0))
    except (AttributeError, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# 4. Verification - only values that are really in the payslip survive
# --------------------------------------------------------------------------- #
@dataclass
class ExtractedField:
    field: str
    label: str
    value: float | str
    evidence: str


@dataclass
class PayslipExtraction:
    fields: list[ExtractedField] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)   # labels
    rejected: list[str] = field(default_factory=list)    # human-readable reasons

    def as_dict(self) -> dict[str, Any]:
        return {"fields": [f.__dict__ for f in self.fields], "not_found": self.not_found,
                "rejected": self.rejected}


def _numbers_in(s: str) -> set[float]:
    return {_num(m) for m in _NUMBER.findall(s)}


def _evidence_line(text: str, quoted: str, value: float) -> Optional[str]:
    """The payslip line that holds `value` - the model's quote if it is genuine."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    lines = [ln for ln in text.splitlines() if value in _numbers_in(ln)]
    if quoted:
        q = norm(quoted)
        for ln in lines:
            if q and (q in norm(ln) or norm(ln) in q):
                return ln.strip()
    return lines[0].strip() if lines else None


_SOURCE = {"base_salary": "base_salary", "pension_pct": "pension_pct",
           "hishtalmut_pct": "hishtalmut_pct", "credit_points": "credit_points",
           "current_participation": "car_participation"}


def _verify_occupation(raw_occ: Any, text: str, config: AppConfig,
                       res: PayslipExtraction) -> None:
    from .tools import resolve_occupation

    label = FIELD_LABELS["occupation"]
    if not raw_occ or not isinstance(raw_occ, str):
        res.not_found.append(label)
        return
    norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    line = next((ln.strip() for ln in text.splitlines() if norm(raw_occ) in norm(ln)), None)
    occ = resolve_occupation(config, raw_occ, fuzzy=True)
    if line is None:
        res.rejected.append(f"{label}: '{raw_occ}' לא נמצא בתלוש")
    elif occ is None:
        res.rejected.append(f"{label}: '{raw_occ}' אינו בטבלת העיסוקים - יש לציין עיסוק בצ'אט")
    else:
        res.fields.insert(0, ExtractedField("occupation", label, occ, line))


def verify(raw: dict[str, Any], text: str, config: AppConfig) -> PayslipExtraction:
    res = PayslipExtraction()
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}

    def check(name: str, key: str, value: Any) -> Optional[tuple[float, str]]:
        label = FIELD_LABELS[name].split(" (")[0]
        try:
            value = float(value)
        except (TypeError, ValueError):
            res.rejected.append(f"{label}: ערך לא מספרי")
            return None
        line = _evidence_line(text, str(evidence.get(key, "")), value)
        if line is None:
            res.rejected.append(f"{label}: הערך {value:g} לא נמצא בתלוש")
            return None
        lo, hi = PAYSLIP_FIELDS[name]
        if not lo <= value <= hi:
            res.rejected.append(f"{label}: הערך {value:g} לא סביר")
            return None
        return value, line

    for name, key in _SOURCE.items():
        if raw.get(key) is None:
            res.not_found.append(FIELD_LABELS[name])
            continue
        ok = check(name, key, raw[key])
        if ok:
            res.fields.append(ExtractedField(name, FIELD_LABELS[name], *ok))

    items = raw.get("commission_items") or []
    good = [ok for v in (items if isinstance(items, list) else [items])
            if (ok := check("commissions_bonuses", "commission_items", v))]
    if good:
        res.fields.insert(1, ExtractedField(
            "commissions_bonuses", FIELD_LABELS["commissions_bonuses"],
            round(sum(v for v, _ in good), 2), " | ".join(line for _, line in good)))
    else:
        res.not_found.append(FIELD_LABELS["commissions_bonuses"])
    _verify_occupation(raw.get("occupation"), text, config, res)
    return res


def extract_payslip(data: bytes, llm: LLMClient, config: AppConfig) -> PayslipExtraction:
    text = redact(read_pdf_text(data))
    return verify(llm_extract(text, llm), text, config)
