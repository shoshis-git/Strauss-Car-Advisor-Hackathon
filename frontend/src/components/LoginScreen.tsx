import { useState, type FormEvent } from "react";
import { api, ApiError, authOf, type Auth, type LoginRequest } from "../api";

interface Props {
  onLogin: (auth: Auth) => void;
}

/** "7" / "e-7" / "E-007" -> "E-007" (the format of the employee numbers) */
export function normalizeEmpId(raw: string): string {
  const t = raw.trim().toUpperCase();
  const m = t.match(/^(?:E-?)?(\d{1,3})$/);
  return m ? `E-${m[1].padStart(3, "0")}` : t;
}

const inputClass =
  "w-full rounded-2xl border border-white bg-white/90 px-4 py-3 text-ink-900 shadow-sm ring-1 ring-ink-100 " +
  "placeholder:text-ink-300 focus:border-lilac-300 focus:outline-none focus:ring-4 focus:ring-lilac-100 disabled:opacity-60";

/** The app's gate for employees: employee number + code, or continue as a guest. */
export function LoginScreen({ onLogin }: Props) {
  const [empId, setEmpId] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"employee" | "guest" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doLogin = async (body: LoginRequest, kind: "employee" | "guest") => {
    setBusy(kind);
    setError(null);
    try {
      onLogin(authOf(await api.login(body)));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = normalizeEmpId(empId);
    if (!id || !code.trim()) {
      setError("נא למלא מספר עובד וקוד");
      return;
    }
    setEmpId(id);
    void doLogin({ emp_id: id, code: code.trim() }, "employee");
  };

  const locked = busy !== null;

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 items-stretch gap-6 py-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      {/* RTL: the form is on the right */}
      <section
        className="rounded-[2rem] bg-white/80 p-7 shadow-[0_16px_40px_-18px_rgb(110_86_180/0.35)] ring-1 ring-white"
        aria-labelledby="login-title"
      >
        <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-linear-to-br from-lilac-300 via-blush-200 to-peach-200 text-2xl">
          🔐
        </span>
        <h2 id="login-title" className="font-display text-3xl font-semibold text-ink-900">
          כניסה ליועץ הרכב
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-500">
          עם מספר עובד, היועץ טוען את הנתונים שלך מראש - שכר, קבוצת זכאות והרכב הנוכחי - ושואל רק מה שחסר.
        </p>

        <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-ink-700">מספר עובד</span>
            <input
              className={`${inputClass} num text-start`}
              dir="ltr"
              value={empId}
              onChange={(e) => setEmpId(e.target.value)}
              placeholder="E-001"
              autoComplete="username"
              disabled={locked}
              aria-label="מספר עובד"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-ink-700">קוד</span>
            <input
              className={`${inputClass} num text-start`}
              dir="ltr"
              type="password"
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="••••"
              autoComplete="current-password"
              disabled={locked}
              aria-label="קוד"
            />
          </label>

          {error && (
            <p className="rounded-2xl bg-blush-50 px-4 py-2.5 text-sm text-blush-700 ring-1 ring-blush-200" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={locked}
            className="w-full rounded-full bg-lilac-500 px-5 py-3 font-display text-base font-semibold text-white shadow-md shadow-lilac-300/50 transition hover:bg-lilac-600 disabled:opacity-60"
          >
            {busy === "employee" ? "נכנסים…" : "כניסה עם מספר עובד"}
          </button>
          <button
            type="button"
            disabled={locked}
            onClick={() => void doLogin({ guest: true }, "guest")}
            className="w-full rounded-full bg-white px-5 py-3 font-display text-base font-semibold text-ink-700 ring-1 ring-ink-100 transition hover:bg-lilac-50 hover:ring-lilac-200 disabled:opacity-60"
          >
            {busy === "guest" ? "פותחים שיחה…" : "המשך כאורח"}
          </button>
          <p className="text-center text-xs text-ink-400">כאורח מתחילים שיחה ריקה ומספרים ליועץ את הנתונים בעצמכם.</p>
        </form>
      </section>

      {/* welcome panel: fills the middle of the page */}
      <section
        className="relative flex flex-col items-center overflow-hidden rounded-[2.5rem] bg-linear-to-br from-lilac-100 via-blush-50 to-peach-100 px-6 py-10 text-center ring-1 ring-white sm:px-10 lg:min-h-[34rem] lg:justify-center"
        aria-labelledby="welcome-title"
      >
        <span aria-hidden="true" className="pointer-events-none absolute -top-16 -left-16 h-56 w-56 rounded-full bg-lilac-200/60 blur-3xl" />
        <span aria-hidden="true" className="pointer-events-none absolute -right-20 -bottom-20 h-64 w-64 rounded-full bg-peach-200/70 blur-3xl" />
        <span aria-hidden="true" className="pointer-events-none absolute top-1/3 right-10 h-32 w-32 rounded-full bg-mint-100/80 blur-2xl" />

        <div className="relative flex flex-col items-center">
          <span className="flex h-24 w-24 items-center justify-center rounded-[2rem] bg-white/80 text-ink-900 shadow-[0_18px_40px_-16px_rgb(110_86_180/0.45)] ring-1 ring-white">
            <svg viewBox="0 0 24 24" className="h-12 w-12 text-lilac-600" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              <path d="M5 16V11.5L7 7h10l2 4.5V16" strokeLinejoin="round" />
              <path d="M3.5 16h17" strokeLinecap="round" />
              <circle cx="7.5" cy="16.5" r="1.6" fill="currentColor" stroke="none" />
              <circle cx="16.5" cy="16.5" r="1.6" fill="currentColor" stroke="none" />
            </svg>
          </span>

          <span className="chip mt-6 bg-white/80 text-lilac-700">ברוכים הבאים</span>
          <h2 id="welcome-title" className="mt-3 font-display text-4xl leading-tight font-semibold text-ink-900 sm:text-5xl">
            הרכב הבא שלך,
            <span className="block bg-linear-to-l from-lilac-600 via-blush-500 to-peach-300 bg-clip-text text-transparent">
              במספרים ברורים
            </span>
          </h2>
          <p className="mt-4 max-w-md text-base leading-relaxed text-ink-600">
            יועץ הרכב מראה לך אילו רכבים מותרים לך, וכמה יישאר לך נטו בכל אחד מהם - לפני שמחליטים.
          </p>

          <ul className="mt-8 grid w-full max-w-xl grid-cols-1 gap-3 text-start sm:grid-cols-3">
            {[
              { icon: "🚗", title: "רק מה שמותר לך", text: "רכבים לפי קבוצת הזכאות שלך" },
              { icon: "💰", title: "נטו בשקל", text: "השוואה מול הרכב של היום" },
              { icon: "📄", title: "תלוש שכר", text: "העלאה במקום הקלדה" },
            ].map((f) => (
              <li key={f.title} className="rounded-2xl bg-white/75 p-4 shadow-sm ring-1 ring-white backdrop-blur">
                <span className="text-2xl" aria-hidden="true">
                  {f.icon}
                </span>
                <span className="mt-2 block font-display font-semibold text-ink-900">{f.title}</span>
                <span className="mt-0.5 block text-xs text-ink-500">{f.text}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
