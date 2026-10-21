import { useCallback, useEffect, useState } from "react";
import { api, ApiError, getRepKey, setRepKey, type RepAnswerResponse, type Ticket } from "../api";
import { ils, signedIls, timeOf } from "../format";

const POLL_MS = 4000;

interface Props {
  onCountChange?: (n: number) => void;
}

function TicketRow({ t, onAnswered }: { t: Ticket; onAnswered: (r: RepAnswerResponse) => void }) {
  const [answer, setAnswer] = useState("");
  const [participation, setParticipation] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const numeric = t.rep_field === "target_participation" || t.rep_field === "current_participation";

  async function submit() {
    setSending(true);
    setError(null);
    try {
      const value = participation.trim() === "" ? undefined : Number(participation);
      const text = answer.trim() || (value !== undefined ? `השתתפות מאושרת: ${value} ₪` : "");
      onAnswered(await api.answer(t.session_id, text, value));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const ready = answer.trim() !== "" || participation.trim() !== "";
  return (
    <tr className="border-t border-slate-100 align-top">
      <td className="px-3 py-3">
        <div className="font-semibold">{t.employee_name ?? "עובד/ת (שיחה חופשית)"}</div>
        <div className="num text-start text-[11px] text-slate-500" title={t.session_id}>
          {t.session_id.slice(0, 22)}
        </div>
        <div className="mt-1 text-xs text-slate-500">
          קבוצה {t.profile.eligibility_group ?? "?"} · שכר {ils(t.profile.base_salary)}
        </div>
      </td>
      <td className="px-3 py-3 text-sm">
        <div>
          <span className="text-slate-500">היום: </span>
          {t.current_car ? `${t.current_car.model_name}` : "ויתור / לא ידוע"}
          {t.current_participation !== null && (
            <span className="text-slate-500"> (השתתפות {ils(t.current_participation)})</span>
          )}
        </div>
        <div className="mt-1 font-semibold">
          <span className="font-normal text-slate-500">מבוקש: </span>
          {t.target_car ? `${t.target_car.model_name} (${t.target_car.car_id})` : t.target_action === "waiver" ? "ויתור" : "—"}
        </div>
        {t.target_car && (
          <div className="text-xs text-slate-500">
            ברירת מחדל בקטלוג: {ils(t.target_car.default_participation)} · {t.target_car.drive}
          </div>
        )}
      </td>
      <td className="max-w-xs px-3 py-3 text-sm">
        <p className="font-medium text-amber-900">{t.rep_question}</p>
        {t.last_employee_message && (
          <p className="mt-1 text-xs text-slate-500">״{t.last_employee_message}״</p>
        )}
        <p className="num mt-1 text-start text-[11px] text-slate-400">{timeOf(t.waiting_since)}</p>
      </td>
      <td className="px-3 py-3">
        <div className="flex min-w-[14rem] flex-col gap-2">
          {numeric && (
            <label className="flex items-center gap-2 text-sm">
              <span className="whitespace-nowrap text-slate-600">השתתפות (₪)</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                value={participation}
                onChange={(e) => setParticipation(e.target.value)}
                className="num w-24 rounded-lg border border-slate-300 px-2 py-1"
                aria-label={`השתתפות מאושרת עבור ${t.employee_name ?? t.session_id}`}
              />
            </label>
          )}
          <input
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="תשובה לעובד/ת (למשל: אושרה השתתפות 350 ₪)"
            className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
            aria-label={`תשובה עבור ${t.employee_name ?? t.session_id}`}
          />
          <button
            type="button"
            disabled={!ready || sending}
            onClick={submit}
            className="rounded-lg bg-teal-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-40"
          >
            {sending ? "שולח…" : "שליחה וסגירת הפנייה"}
          </button>
          {error && <p className="text-xs text-red-700">{error}</p>}
        </div>
      </td>
    </tr>
  );
}

function RepLogin({ error, onSubmit }: { error: string | null; onSubmit: (key: string) => void }) {
  const [key, setKey] = useState("");
  return (
    <form
      className="mx-auto max-w-md space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault();
        if (key.trim()) onSubmit(key.trim());
      }}
    >
      <h2 className="text-lg font-bold">כניסת נציג/ת שכר ותחבורה</h2>
      <p className="text-sm text-slate-500">המסך מציג נתוני שכר של עובדים ולכן דורש מפתח נציג.</p>
      <input
        type="password"
        autoComplete="off"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        aria-label="מפתח נציג"
        className="num w-full rounded-lg border border-slate-300 px-3 py-2"
      />
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}
      <button
        type="submit"
        disabled={!key.trim()}
        className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-40"
      >
        כניסה
      </button>
    </form>
  );
}

export function RepDashboard({ onCountChange }: Props) {
  const [hasKey, setHasKey] = useState(() => getRepKey() !== null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [last, setLast] = useState<RepAnswerResponse | null>(null);

  const logout = useCallback((reason: string | null) => {
    setRepKey(null);
    setHasKey(false);
    setTickets([]);
    setAuthError(reason);
    onCountChange?.(0);
  }, [onCountChange]);

  const load = useCallback(async () => {
    try {
      const t = await api.tickets();
      setTickets(t);
      setError(null);
      onCountChange?.(t.length);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 503)) logout(e.message);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, [onCountChange, logout]);

  useEffect(() => {
    if (!hasKey) return;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load, hasKey]);

  if (!hasKey) {
    return (
      <RepLogin
        error={authError}
        onSubmit={(key) => {
          setRepKey(key);
          setAuthError(null);
          setHasKey(true);
        }}
      />
    );
  }

  function answered(r: RepAnswerResponse) {
    setLast(r);
    setTickets((ts) => ts.filter((t) => t.session_id !== r.session_id));
    load();
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold">פניות ממתינות לנציג/ת שכר ותחבורה</h2>
          <p className="text-sm text-slate-500">מתעדכן אוטומטית. מענה משחרר את שיחת העובד ומפעיל את החישוב.</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100"
          >
            ↻ רענון
          </button>
          <button
            type="button"
            onClick={() => logout(null)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100"
          >
            יציאה
          </button>
        </div>
      </div>

      {last && (
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900" role="status">
          ✓ הפנייה נסגרה ({last.employee_name ?? "שיחה חופשית"}).
          {last.comparison?.is_eligible && (
            <>
              {" "}נטו היום <span className="num font-semibold">{ils(last.comparison.net_today)}</span> → בחלופה{" "}
              <span className="num font-semibold">{ils(last.comparison.net_alt)}</span> (
              <span className="num font-semibold">{signedIls(last.comparison.net_diff)}</span>). העובד/ת רואה את
              התוצאה בשיחה.
            </>
          )}
        </div>
      )}
      {error && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-start">
          <thead className="bg-slate-50 text-sm text-slate-600">
            <tr>
              <th className="px-3 py-2 text-start font-semibold">עובד/ת ומזהה שיחה</th>
              <th className="px-3 py-2 text-start font-semibold">רכב</th>
              <th className="px-3 py-2 text-start font-semibold">השאלה</th>
              <th className="px-3 py-2 text-start font-semibold">מענה</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((t) => (
              <TicketRow key={t.session_id} t={t} onAnswered={answered} />
            ))}
            {loaded && tickets.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-10 text-center text-slate-500">
                  אין פניות ממתינות 🎉
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
