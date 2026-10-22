# יועץ הרכב של שטראוס

יועץ שיחתי לעובדים שמשווה בין רכב החברה הנוכחי לבין חלופה (רכב אחר מהקטלוג או ויתור על רכב) ומראה בשקלים מה קורה לשכר הנטו החודשי.

העובד מתאר את מצבו בשפה חופשית, או מעלה תלוש שכר. היועץ אוסף את הנתונים החסרים, בודק זכאות לפי נוהל החברה ומחזיר השוואה מפורטת. כשחסר נתון שאי אפשר לנחש (למשל גובה השתתפות רשמי), השאלה עוברת לנציג/ת שכר, והשיחה ממתינה עד שמתקבלת תשובה.

## עקרונות

- **המספרים לא מגיעים ממודל השפה.** כל החישובים נעשים במנוע דטרמיניסטי (`backend/engine`). מודל השפה רק מנהל את השיחה וקורא לכלים.
- **שום כלל עסקי לא כתוב בקוד.** תקציבים, סדר קבוצות, מסלול משפחה, ויתור, שיטת הנטו וכדומה נקראים מ-`data/rules.json` ומ-`data/challenge_data.xlsx`. החלפת הקבצים מעדכנת את ההתנהגות, וגם את מה שהיועץ מסביר לעובד.
- **שווי השימוש** נלקח מהמאגר הרשמי של רשות המסים לשנת 2026 (בערך 78 אלף רשומות). החיפוש נעשה לפי המפתח הרשמי: שנת מס, שנת רישום, סוג רכב, תוצר ודגם.
- **פרטיות.** תלוש השכר נקרא בזיכרון ולא נשמר. לפני שטקסט כלשהו נשלח למודל, מוסתרים ממנו פרטים מזהים: ת"ז, חשבון בנק, כתובת, טלפון, דוא"ל ושם. נכנסים למצב השיחה רק ערכים שהעובד אישר.

## ארכיטקטורה

הסבר מפורט, כולל תרשימים ומנגנוני ההגנה מפני המצאות: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
 Frontend (React + Vite)  ──HTTP──►  Backend (FastAPI)
   מסך עובד / מסך נציג                │
                                      ├─ agent/   לולאת שיחה: LLM ⇄ כלים, העברה לנציג
                                      ├─ engine/  זכאות, חישוב נטו, השוואה (דטרמיניסטי)
                                      ├─ data_loader/  כללים, קטלוג, מאגר רשות המסים
                                      └─ state    שיחות: SQLite מקומית / Postgres בייצור
```

| שכבה | טכנולוגיה | ענן |
|---|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind | Vercel |
| Backend | Python 3.12, FastAPI, Pydantic, pandas | Render |
| שיחות | SQLite (מקומית) או Postgres | Supabase |
| מאגר שווי שימוש | SQLite שנבנה מה-CSV | (חלק מה-backend) |
| מודל שפה | Gemini, OpenAI, Anthropic, Groq, OpenRouter, Ollama או כל שרת תואם OpenAI | |

### מבנה התיקיות

```
backend/
  main.py              יצירת האפליקציה, CORS, כותרות אבטחה
  api/                 נתיבי HTTP ומודלי בקשה/תגובה
  agent/
    graph.py           זרימת השיחה והעברה לנציג
    tools.py           הכלים של הסוכן (update_profile, get_allowed_cars, calculate_comparison, ...)
    prompts.py         System prompt בעברית, נבנה מהכללים בכל תור
    llm.py             שכבה אחידה לכל ספקי ה-LLM, כולל גיבוי בין ספקים
    llm_check.py       בדיקת חיבור לספקים שהוגדרו
    payslip.py         קריאת תלוש: טקסט, הסתרת פרטים, חילוץ שדות, אימות
    rule_based.py      "מוח" דטרמיניסטי בלי LLM (לבדיקות ולעבודה offline)
    state.py           מצב השיחה ושמירתו
  engine/              eligibility.py, calculator.py, comparator.py
  data_loader/         טעינת rules.json, האקסל, מאגר רשות המסים
  tests/               בדיקות pytest (שלבים 1 עד 5) ותלושים סינתטיים
data/                  קבצי המקור: rules.json, challenge_data.xlsx, tax_authority_2026.csv, car_aliases.json
frontend/src/
  App.tsx              לשוניות: עובד / נציג
  components/          ChatPanel, ComparisonDashboard, PayslipReview, RepDashboard, DemoSelector
  api.ts               לקוח טיפוסי ל-API
render.yaml            הגדרת השירות ב-Render
```

## הרצה מקומית

דרישות: Python 3.12 ו-Node.js 20 ומעלה.

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env            # ממלאים לפחות LLM_PROVIDER, LLM_MODEL, LLM_API_KEY
python -m backend.data_loader.build_data   # אופציונלי: נבנה גם לבד בהפעלה הראשונה
uvicorn backend.main:app --reload --port 8000
```

- תיעוד API אינטראקטיבי: http://localhost:8000/docs
- בדיקת חיבור למודל השפה (מפתחות לא מודפסים): `python -m backend.agent.llm_check`
- אחרי שינוי ב-`.env` צריך להפעיל את השרת מחדש. `--reload` עוקב רק אחרי קבצי `.py`.

### Frontend

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

כתובת ה-backend נקבעת לפי `VITE_API_BASE`, וברירת המחדל היא `http://localhost:8000`. כדי לשנות אותה מעתיקים את `frontend/.env.example` ל-`frontend/.env.local`.

## משתני סביבה (backend)

הרשימה המלאה, עם הסברים, נמצאת ב-[.env.example](.env.example).

| משתנה | תפקיד |
|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` | ספק המודל, שם המודל ומפתח. `rules` = בלי LLM (לבדיקות בלבד) |
| `LLM_BASE_URL` | כתובת חלופית; חובה עבור `openai_compatible` |
| `LLM_PROVIDER_2` ... `_5` | ספקים לגיבוי: אם אחד נכשל (מכסה, קרדיט, תקלה), הבא בתור עונה |
| `LLM_FALLBACK` | `none` (ברירת מחדל): העובד רואה את סיבת התקלה. `rules`: המוח הדטרמיניסטי עונה במקום המודל |
| `SECRET_KEY` | חתימת טוקני הכניסה של העובדים. אם הוא ריק, נוצר מפתח אקראי בכל הפעלה, וכולם צריכים להיכנס מחדש. החלפה שלו מנתקת את כולם |
| `REP_API_KEY` | מפתח מסך הנציג (נשלח בכותרת `X-Rep-Token`). אם הוא ריק, מסך הנציג סגור |
| `PAYSLIP_VISION` | `on`: תלוש בלי טקסט קריא (סריקה) נקרא מתמונת העמוד על ידי המודל |
| `DATABASE_URL` | אם הוא ריק, השיחות נשמרות ב-SQLite מקומית. בייצור: ה-URI של ה-Transaction pooler ב-Supabase |
| `CORS_ORIGINS` | כתובות דפדפן נוספות שמותר להן לפנות לשרת, מופרדות בפסיקים. localhost תמיד מותר |
| `API_DOCS` | `0` מכבה את `/docs` (מומלץ בייצור) |
| `STRAUSS_DATA_DIR` | תיקיית נתונים חלופית (למשל נתוני פיילוט אמיתיים) |

## בדיקות

```bash
pytest                            # backend: נתונים, מחשבון, סוכן, API, תלושים
cd frontend && npm test           # frontend: Vitest + Testing Library
```

- הבדיקות של ה-backend רצות offline עם `RuleBasedLLM`, שמדבר באותו פרוטוקול קריאה לכלים כמו מודל אמיתי.
- המחשבון נבדק מול חישובי הייחוס של 6 העובדים לדוגמה באקסל, בכל 9 העמודות.
- בדיקה מול מודל אמיתי רצה רק כש-`LLM_PROVIDER` מוגדר.
- בדיקה מול Postgres אמיתי רצה רק כש-`TEST_DATABASE_URL` מוגדר.

## API (עיקרי)

| Method | נתיב | תיאור |
|---|---|---|
| GET | `/api/health` | בדיקת חיים |
| GET | `/api/catalog` | קטלוג רכבים ועובדי דמו |
| POST | `/api/auth/login` | כניסה: `{emp_id, code}` (עובדי הדמו: קוד 1234) או `{guest: true}`. מחזיר טוקן חתום שקשור לשיחה חדשה |
| POST | `/api/chat` | הודעת עובד והתשובה של היועץ (דורש טוקן) |
| GET | `/api/session/{id}` | מצב שיחה (דורש טוקן של אותה שיחה) |
| POST | `/api/demo/{E-001}` | השאלה המקורית של עובד הדמו מהאקסל, בשיחה של אותו עובד (דורש טוקן שלו) |
| GET / POST | `/api/session/{id}/options`, `/alternative` | בחירת חלופה |
| GET | `/api/session/{id}/ranking` | כל האפשרויות: הנטו לכל חלופה מותרת, מדורג מהמשתלם ביותר |
| POST | `/api/payslip/extract`, `/api/payslip/apply` | העלאת תלוש ואישור הערכים |
| GET | `/api/rep/tickets` | פניות שממתינות לנציג (דורש `X-Rep-Token`) |
| POST | `/api/rep/tickets/{id}/answer` | תשובת נציג; השיחה ממשיכה (דורש `X-Rep-Token`) |
| GET | `/api/llm/check` | בדיקת ספקי LLM (דורש `X-Rep-Token`) |

## העלאה לענן

**1. Supabase (שיחות)**
- יוצרים פרויקט.
- ב-**Connect → Transaction pooler** מעתיקים את ה-URI (פורט 6543) בדיוק כמו שהוא. מחליפים רק את `[YOUR-PASSWORD]`, בלי הסוגריים.
- הטבלה `conversations` נוצרת אוטומטית, עם RLS מופעל.
- משתמשים ב-pooler ולא ב-Direct connection: ה-Direct עובד רק ב-IPv6, ו-Render לא תומך בזה.

**2. Render (backend)**
- **New → Blueprint** על ה-repo. Render קורא את [render.yaml](render.yaml).
- או ידנית, כ-Web Service:

  | שדה | ערך |
  |---|---|
  | Root Directory | (ריק) |
  | Build Command | `pip install -r requirements.txt && python -m backend.data_loader.build_data` |
  | Start Command | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |
  | Health Check Path | `/api/health` |

- משתנים: `PYTHON_VERSION=3.12.4`, `API_DOCS=0`, `DATABASE_URL`, משתני `LLM_*`, `REP_API_KEY`, `CORS_ORIGINS`.

**3. Vercel (frontend)**
- Root Directory: `frontend`.
- משתנה: `VITE_API_BASE=https://<service>.onrender.com`.

**4.** חוזרים ל-Render ומגדירים `CORS_ORIGINS=https://<project>.vercel.app` (בלי `/` בסוף).


