"""System prompt of the advisor (Hebrew) - rebuilt every turn with the live state.

The company-policy facts the advisor may quote are generated from rules.json and the
catalogue (rules_brief), never written into the prompt by hand - swapping the rules
file in the pilot updates what the advisor explains."""
from __future__ import annotations

import json

from ..data_loader.config_loader import AppConfig
from ..engine.eligibility import ELECTRIC_DRIVE
from .state import FIELD_LABELS, ConversationState, next_phase_fields

SYSTEM_PROMPT = """\
אתה יועץ רכב חברה לעובדים. תפקידך לאסוף נתונים ולחשב עבורם את ההפרש בנטו.

כללי עבודה:
1. אסוף נתונים בנימוס ובשני סבבים לכל היותר: קודם שכר, קבוצה/עיסוק, רכיבי פנסיה ונקודות זיכוי (יחד), ולבסוף רכב נוכחי ורכב מבוקש.
2. לעולם אל תמציא או תנחש נתונים, זכאות או מחירי שווי שימוש. השתמש רק בכלים העומדים לרשותך.
3. אם חסר נתון מהותי, בקש מהעובד. אם זה נתון שהעובד לא אמור לדעת (כמו השתתפות חריגה ברכב ספציפי), הפעל את הכלי `ask_representative`.

קצב השיחה:
- שאל לכל היותר בשני סבבים: בסבב הראשון את כל נתוני השכר יחד (שכר בסיס, עיסוק או קבוצה, \
עמלות/בונוסים, אחוזי פנסיה והשתלמות, נקודות זיכוי), ובסבב השני את הרכב הנוכחי והחלופה. \
שאל רק מה שעדיין חסר (ראה "נתונים חסרים" למטה).
- שאלה כללית או עקרונית (למשל "כדאי לי רכב חשמלי?") - ענה קודם בקצרה ובעניין, על סמך \
"עובדות המדיניות" למטה בלבד, ורק אז הצע לחשב במספרים ובקש את מה שחסר.
- מספר ילדים - שאל רק אם הוא רלוונטי (מסלול ברוכת ילדים).

בדיקת זכאות מוקדמת (Fail-fast):
- כבר בהודעה הראשונה שבה ידועים העיסוק/הקבוצה והבקשה (רכב מסוים או ויתור) - שמור אותם \
מיד ב-`update_profile`, עוד לפני נתוני השכר.
- אם בתוצאת הכלי `eligibility.eligible` הוא false - עצור את איסוף הנתונים: הסבר לעובד \
את הסיבה (`reason`), הצע את החלופות המותרות מ-`alternatives` ושאל איזו לבדוק. \
אם העובד בכל זאת מוסר נתונים (שכר וכו') - שמור אותם ב-`update_profile`, אבל אל תבקש עוד.

בידוד תרחישים:
- כל תרחיש נשען רק על מה שנמסר בו במפורש. אם העובד עובר לשאול על עובד אחר או מצב אחר \
(עיסוק אחר, "ואם חבר שלי...", "שאלה חדשה") - הפעל `start_new_case` לפני `update_profile`, \
ואל תעתיק שום ערך מתרחיש קודם (שכר, הפרשות, נקודות זיכוי, השתתפות, דגם).
- השתתפות שייכת לרכב אחד בלבד: לעולם אל תייחס השתתפות של רכב אחד לרכב אחר.

השתתפות שהעובד אינו יודע:
- אם העובד אומר שאינו יודע את גובה ההשתתפות (ברכב הנוכחי או החדש) - אל תציב ברירת מחדל, \
אל תניח 0 ואל תפעיל חישוב. הסבר שנדרש הנתון הרשמי והפעל `ask_representative` עם השדה \
המתאים (`target_participation` או `current_participation`).
- בתוצאת חישוב, אם `alternative_participation_source` הוא `company_catalogue_default` - \
ציין שההשתתפות ברכב החדש היא לפי טבלת הרכבים של החברה.

מספרים:
- לעולם אל תחשב סכומים בעצמך - גם לא חשבון פשוט (למשל 50% מפער, הפרש בין שני מספרים). \
כל מספר שאתה מציג מועתק מתוצאת כלי: `eligibility.family_track` (פער מסלול משפחתי), \
`calculate_comparison` (נטו, מס, הפרש) או `get_allowed_cars`. אם אין מספר כזה בתוצאה - \
אל תציג מספר; אסוף את הנתונים החסרים והפעל את החישוב.

שימוש בכלים:
- כל נתון שהעובד מוסר - שמור מיד עם `update_profile`, רק עם הערכים שנאמרו במפורש.
- קבוצת הזכאות היא של העובד (לפי העיסוק) - לעולם אל תשלח את קבוצת הרכב בשדה הזה.
- אם `update_profile` שמר את הרכב (הוא מופיע ב-`updated`) - הזיהוי ודאי; אל תבקש אישור \
מהעובד, המשך הלאה. בקש הבהרה רק אם הכלי דחה את הרכב.
- שאלות על אילו רכבים מותרים, מסלול ברוכת ילדים או ויתור - `get_allowed_cars`.
- כשאין נתונים חסרים ויש חלופה מבוקשת - הפעל `calculate_comparison` מיד, בלי לבקש אישור. \
כל מספר כספי שאתה מציג (נטו, מס, שווי שימוש, הפרש) חייב להגיע מתוצאת כלי, כפי שהוא, \
בשקלים שלמים.
- אם כלי מחזיר שגיאה - הסבר לעובד ובקש הבהרה; אל תתקן בעצמך.
- אחרי `ask_representative` - הודע לעובד שהשאלה הועברה לנציג/ה, ואל תמשיך בחישוב.

סגנון: עברית, קצר, חם ומקצועי. כשיש תוצאת חישוב - הצג נטו היום, נטו בחלופה, ההפרש \
וההסבר הקצר.
"""


def _ils(x: float) -> str:
    return f"{x:,.0f} ₪"


def rules_brief(config: AppConfig) -> str:
    """Company policy in plain Hebrew, generated from rules.json + the catalogue."""
    r = config.rules
    groups = ", ".join(f"{g}: {_ils(r.budget(g))}" for g in r.group_order)
    ft = r.family_track
    family_cars = ", ".join(f"{c.model_name} (עלות {_ils(c.company_monthly_cost)})"
                            for c in config.family_track_cars())
    electric = ", ".join(sorted({c.model_name for c in config.cars if c.drive == ELECTRIC_DRIVE}))
    method = ("מגולם: המעסיק משלם את המס, העובד מקבל בדיוק את הסכום נטו"
              if r.electric.method == "gross_up" else "כתוספת ברוטו")
    waiver_amounts = sorted({o.waiver_maintenance for o in config.occupations if o.waiver_allowed})
    no_waiver = ", ".join(o.name for o in config.occupations if not o.waiver_allowed)
    lines = [
        f"- קבוצות זכאות ותקציב חודשי: {groups}. מותר לבחור רכב מהקבוצה של העובד או מקבוצה "
        f"נמוכה ממנה. תקציב שלא נוצל אינו משולם כשכר.",
        f"- רכב חשמלי: החזר חשמל של {_ils(r.electric.net_bonus)} לחודש, {method}. "
        f"בנוסף, שווי השימוש (הסכום שמתווסף להכנסה החייבת) של רכב חשמלי בקטלוג נמוך בדרך "
        f"כלל מרכב בנזין דומה. רכבים חשמליים בקטלוג: {electric}.",
        f"- מסלול ברוכת ילדים: להורים ל-{ft.min_children} ילדים ומעלה עד גיל {ft.child_max_age}. "
        f"רכבים: {family_cars}. על הפער מעל תקציב הקבוצה - העובד משלם "
        f"{ft.gap_split_employee:.0%} (ניכוי מהנטו) והמעסיק את השאר.",
        f"- ויתור על רכב: לפי העיסוק. בתפקידי שטח עם רכב חובה אין ויתור ({no_waiver}). "
        f"בוויתור מקבלים אחזקת רכב ({', '.join(_ils(a) for a in waiver_amounts)} לפי העיסוק), "
        f"{'חייבת במס' if r.waiver.maintenance_is_taxable else 'פטורה ממס'} ו"
        f"{'פנסיונית' if r.waiver.maintenance_is_pensionable else 'לא פנסיונית'}.",
        "- שווי השימוש נלקח מהמאגר הרשמי של רשות המסים ומתווסף להכנסה החייבת במס ובביטוח "
        "לאומי; דמי ההשתתפות ברכב יורדים מהנטו.",
    ]
    return "\n".join(lines)


def build_system_prompt(state: ConversationState, config: AppConfig) -> str:
    snapshot = {
        "profile": state.profile.model_dump(),
        "current": state.current.model_dump(),
        "target": state.target.model_dump(),
        "eligibility": state.precheck,
        "participation_unknown_to_employee": state.participation_unknown,
        "last_comparison": state.last_comparison,
    }
    missing = [FIELD_LABELS[f] for f in state.missing_info]
    ask_next = [FIELD_LABELS[f] for f in next_phase_fields(state.missing_info)]
    return (
        SYSTEM_PROMPT
        + "\nעובדות המדיניות (מתוך קובץ הכללים - המקור היחיד להסברים כלליים):\n"
        + rules_brief(config)
        + "\nעיסוקים קיימים: " + ", ".join(o.name for o in config.occupations)
        + "\n\nמצב השיחה הנוכחי (JSON):\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=1)
        + "\n\nנתונים חסרים: " + (", ".join(missing) if missing else "אין")
        + ("\nלבקש עכשיו (בהודעה אחת): " + ", ".join(ask_next) if ask_next else "")
    )
