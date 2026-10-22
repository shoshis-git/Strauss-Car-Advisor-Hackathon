import { useState, type FormEvent } from "react";
import { api, ApiError, type Auth, type DemoEmployee, type LoginRequest } from "../api";

interface Props {
  demoEmployees: DemoEmployee[];
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
export function LoginScreen({ demoEmployees, onLogin }: Props) {
  const [empId, setEmpId] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"employee" | "guest" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doLogin = async (body: LoginRequest, kind: "employee" | "guest") => {
    setBusy(kind);
    setError(null);
    try {
      const r = await api.login(body);
      onLogin({
        token: r.token,
        session_id: r.session_id,
        kind: r.kind,
        employee_name: r.employee_name,
        expires_at: r.expires_at,
      });
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

  /** judges: one click fills the form and logs in */
  const demoLogin = (d: DemoEmployee) => {
    if (!d.demo_code) return;
    setEmpId(d.employee_id);
    setCode(d.demo_code);
    void doLogin({ emp_id: d.employee_id, code: d.demo_code }, "employee");
  };

  const locked = busy !== null;

  return (
    <div className="mx-auto grid max-w-5xl grid-cols-1 items-start gap-6 py-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
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

      {demoEmployees.length > 0 && (
        <section
          className="rounded-[2rem] bg-linear-to-br from-peach-100 via-butter-50 to-blush-50 p-6 ring-1 ring-white"
          aria-labelledby="demo-title"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 id="demo-title" className="font-display text-xl font-semibold text-ink-900">
              חשבונות דמו להדגמה
            </h3>
            <span className="chip bg-white/80 text-ink-600">לחיצה = כניסה מיידית</span>
          </div>
          <p className="mt-1 text-xs text-ink-500">
            עובדים פיקטיביים מנתוני האתגר. הקוד של כולם הוא{" "}
            <span className="num font-semibold text-ink-700">{demoEmployees[0].demo_code}</span>.
          </p>
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {demoEmployees.map((d) => (
              <button
                key={d.employee_id}
                type="button"
                disabled={locked || !d.demo_code}
                onClick={() => demoLogin(d)}
                aria-label={`כניסה כ${d.name} (${d.employee_id})`}
                className="rounded-2xl bg-white/85 px-4 py-3 text-start shadow-sm ring-1 ring-white transition hover:-translate-y-0.5 hover:bg-white hover:shadow-md disabled:opacity-50 disabled:hover:translate-y-0"
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-display font-semibold text-ink-900">{d.name}</span>
                  <span className="num text-[11px] text-ink-400">{d.employee_id}</span>
                </span>
                <span className="mt-0.5 block text-xs text-ink-500">{d.occupation}</span>
              </button>
            ))}
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-ink-500">
            בפיילוט הכניסה תהיה עם המשתמש הארגוני של שטראוס (SSO), והנתונים ייטענו ממערכת השכר.
          </p>
        </section>
      )}
    </div>
  );
}
