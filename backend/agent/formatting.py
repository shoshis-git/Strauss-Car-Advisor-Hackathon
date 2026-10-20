"""Hebrew rendering of tool results (shared by the rule-based brain and the demo loader)."""
from __future__ import annotations

from typing import Any


def fmt_ils(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,}"


def format_blocked(precheck: dict[str, Any]) -> str:
    """Fail-fast message: why the request is impossible + permitted alternatives."""
    lines = [f"לא ניתן לבצע את הבקשה ({precheck['requested']}): {precheck['reason']}."]
    alts = precheck.get("alternatives") or []
    if alts:
        lines.append("חלופות מותרות שאפשר לבדוק:")
        lines += [f"• {a['model_name']} ({a['car_id']}, קבוצה {a['group']}, {a['drive']}) - "
                  f"שווי שימוש {fmt_ils(a['value_of_use'])} ₪" for a in alts]
        lines.append("איזו מהן לבדוק עבורך?")
    return "\n".join(lines)


def format_comparison(calc: dict[str, Any]) -> str:
    """calculate_comparison result -> employee-facing message."""
    if not calc.get("is_eligible"):
        return f"החלופה המבוקשת אינה אפשרית: {calc['reason']}"
    return (
        f"החישוב מוכן ({calc['current_label']} ← {calc['alternative_label']}):\n"
        f"נטו היום: {fmt_ils(calc['net_today'])} ₪\n"
        f"נטו בחלופה: {fmt_ils(calc['net_alt'])} ₪\n"
        f"הפרש: {calc['net_diff']:+,} ₪ בחודש\n"
        f"למה: {calc['explanation']}"
    )
