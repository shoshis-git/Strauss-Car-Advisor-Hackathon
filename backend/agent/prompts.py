"""System prompt of the advisor (Hebrew) - rebuilt every turn with the live state."""
from __future__ import annotations

import json

from ..data_loader.config_loader import AppConfig
from .state import FIELD_LABELS, ConversationState, next_phase_fields

SYSTEM_PROMPT = """\
אתה יועץ רכב חברה לעובדים. תפקידך לאסוף נתונים ולחשב עבורם את ההפרש בנטו.

כללי עבודה:
1. אסוף נתונים בהדרגה ובנימוס: קודם שכר וקבוצה/עיסוק, לאחר מכן רכיבי פנסיה ונקודות זיכוי, ולבסוף רכב נוכחי ורכב מבוקש.
2. לעולם אל תמציא או תנחש נתונים, זכאות או מחירי שווי שימוש. השתמש רק בכלים העומדים לרשותך.
3. אם חסר נתון מהותי, בקש מהעובד. אם זה נתון שהעובד לא אמור לדעת (כמו השתתפות חריגה ברכב ספציפי), הפעל את הכלי `ask_representative`.

שימוש בכלים:
- כל נתון שהעובד מוסר - שמור מיד עם `update_profile`, רק עם הערכים שנאמרו במפורש.
- שאלות על אילו רכבים מותרים, מסלול ברוכת ילדים או ויתור - `get_allowed_cars`.
- כשאין נתונים חסרים ויש חלופה מבוקשת - `calculate_comparison`. כל מספר כספי שאתה מציג \
(נטו, מס, שווי שימוש, הפרש) חייב להגיע מתוצאת כלי, כפי שהוא, בשקלים שלמים.
- אם כלי מחזיר שגיאה (למשל רכב שלא בקטלוג) - הסבר לעובד ובקש הבהרה; אל תתקן בעצמך.
- אחרי `ask_representative` - הודע לעובד שהשאלה הועברה לנציג/ה, ואל תמשיך בחישוב.

סגנון: עברית, קצר, חם ומקצועי. שאל עד 2-3 נתונים בכל הודעה. \
כשיש תוצאת חישוב - הצג נטו היום, נטו בחלופה, ההפרש וההסבר הקצר.
"""


def build_system_prompt(state: ConversationState, config: AppConfig) -> str:
    snapshot = {
        "profile": state.profile.model_dump(),
        "current": state.current.model_dump(),
        "target": state.target.model_dump(),
        "last_comparison": state.last_comparison,
    }
    missing = [FIELD_LABELS[f] for f in state.missing_info]
    ask_next = [FIELD_LABELS[f] for f in next_phase_fields(state.missing_info)]
    return (
        SYSTEM_PROMPT
        + "\nקבוצות זכאות קיימות: " + ", ".join(config.rules.group_order)
        + "\n\nמצב השיחה הנוכחי (JSON):\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=1)
        + "\n\nנתונים חסרים: " + (", ".join(missing) if missing else "אין")
        + ("\nהשלב הבא - לבקש: " + ", ".join(ask_next) if ask_next else "")
    )
