import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, getRepKey, setRepKey, type RepAnswerResponse, type Ticket } from "../api";
import { ils, signedIls, timeOf } from "../format";

const POLL_MS = 4000;

interface Props {
  onCountChange?: (n: number) => void;
  onAuthChange?: (authed: boolean) => void;
}

const FIELD_LABEL: Record<string, string> = {
  target_participation: "השתתפות ברכב המבוקש",
  current_participation: "השתתפות ברכב הנוכחי",
};

function minutesSince(iso: string): number | null {
  const t = Date.parse(iso);
  return isNaN(t) ? null : Math.max(0, Math.round((Date.now() - t) / 60000));
}

function WaitBadge({ since }: { since: string }) {
  const m = minutesSince(since);
  if (m === null) return null;
  const urgent = m >= 10;
  return (
    <span className={`chip shrink-0 ${urgent ? "bg-blush-100 text-blush-700" : "bg-baby-100 text-baby-700"}`}>
      {m < 1 ? "ממתין/ה עכשיו" : m < 60 ? `ממתין/ה ${m} דק׳` : `ממתין/ה ${Math.floor(m / 60)} שע׳`}
    </span>
  );
}

const carName = (t: Ticket, which: "current" | "target") => {
  if (which === "current") return t.current_car?.model_name ?? "ויתור / לא ידוע";
  return t.target_car?.model_name ?? (t.target_action === "waiver" ? "ויתור" : "—");
};

/* ---------- sidebar list ---------- */

function TicketCard({ t, active, onSelect }: { t: Ticket; active: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active}
      className={`w-full rounded-3xl p-4 text-start transition ${
        active
          ? "bg-white shadow-[0_12px_30px_-14px_rgb(110_86_180/0.45)] ring-2 ring-lilac-300"
          : "bg-white/70 ring-1 ring-white hover:-translate-y-0.5 hover:bg-white hover:shadow-md"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-display text-base font-semibold text-ink-900">
          {t.employee_name ?? "עובד/ת (שיחה חופשית)"}
        </span>
        <WaitBadge since={t.waiting_since} />
      </div>
      <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-ink-600">{t.last_employee_message ?? t.rep_question}</p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <span className="chip bg-lilac-100 text-lilac-700">קבוצה {t.profile.eligibility_group ?? "?"}</span>
        <span className="chip num bg-peach-100 text-peach-700">
          {carName(t, "current")} → {carName(t, "target")}
        </span>
      </div>
    </button>
  );
}

/* ---------- selected ticket ---------- */

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 rounded-2xl bg-lilac-50/80 px-3 py-2">
      <dt className="text-[11px] text-ink-400">{label}</dt>
      <dd className="mt-0.5 font-semibold text-ink-900">{children}</dd>
    </div>
  );
}

function TicketDetail({ t, onAnswered }: { t: Ticket; onAnswered: (r: RepAnswerResponse) => void }) {
  const [answer, setAnswer] = useState("");
  const [participation, setParticipation] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const numeric = t.rep_field === "target_participation" || t.rep_field === "current_participation";
  const catalogCar = t.rep_field === "current_participation" ? t.current_car : t.target_car;
  const who = t.employee_name ?? t.session_id;

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
  const catalogValue = catalogCar ? String(catalogCar.default_participation) : null;
  const usingCatalog = catalogValue !== null && participation === catalogValue;

  return (
    <div className="space-y-5">
      {/* frozen conversation */}
      <div className="rounded-[2rem] bg-linear-to-br from-lilac-200 via-blush-100 to-peach-100 p-6 shadow-[0_16px_40px_-18px_rgb(110_86_180/0.4)] ring-1 ring-white">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="chip bg-white font-semibold text-blush-700">❄ השיחה מוקפאת</span>
          <span className="text-ink-600">היועץ לא ינחש את הנתון. החישוב ימשיך מיד אחרי התשובה שלך.</span>
        </div>
        <p className="mt-4 font-display text-2xl font-semibold leading-snug text-ink-900 sm:text-3xl">״{t.rep_question}״</p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          {t.rep_field && (
            <span className="chip bg-white/70 text-ink-600">
              שדה חסר: <strong className="font-semibold text-ink-900">{FIELD_LABEL[t.rep_field] ?? t.rep_field}</strong>
            </span>
          )}
          <span className="chip bg-white/70 text-ink-600">
            {t.employee_name ?? "שיחה חופשית"}
            {t.profile.occupation ? ` · ${t.profile.occupation}` : ""}
          </span>
          <span className="chip num bg-white/50 text-ink-400" title={t.session_id}>
            {t.session_id.slice(0, 22)}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <div className="card p-5">
          <h3 className="mb-4 font-display text-lg font-semibold text-ink-900">תיק העובד</h3>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <Fact label="קבוצה">{t.profile.eligibility_group ?? "?"}</Fact>
            <Fact label="ילדים עד 23">
              <span className="num">{t.profile.children_count ?? "—"}</span>
            </Fact>
            <Fact label="שכר בסיס">
              <span className="num">{ils(t.profile.base_salary)}</span>
            </Fact>
            <Fact label="עיסוק">{t.profile.occupation ?? "—"}</Fact>
            <Fact label="רכב היום">
              {carName(t, "current")}
              {t.current_participation !== null && (
                <span className="font-normal text-ink-500">
                  {" "}
                  · השתתפות <span className="num">{ils(t.current_participation)}</span>
                </span>
              )}
            </Fact>
            <Fact label="רכב מבוקש">
              {carName(t, "target")}
              {t.target_car && <span className="num font-normal text-ink-400"> ({t.target_car.car_id})</span>}
              {t.rep_field === "target_participation" && <span className="text-blush-700"> · חסר</span>}
              {t.target_car && (
                <span className="mt-0.5 block text-xs font-normal text-ink-500">
                  ברירת מחדל בקטלוג: {ils(t.target_car.default_participation)} · {t.target_car.drive}
                </span>
              )}
            </Fact>
          </dl>
        </div>

        <div className="card p-5">
          <h3 className="mb-4 font-display text-lg font-semibold text-ink-900">מהשיחה</h3>
          <div className="space-y-2.5 text-sm">
            {t.last_employee_message ? (
              <p className="rounded-3xl rounded-se-md bg-linear-to-br from-lilac-100 to-baby-100 px-4 py-2.5 leading-relaxed text-ink-700">
                <strong className="font-semibold text-ink-900">{t.employee_name?.split(" (")[0] ?? "העובד/ת"}:</strong>{" "}
                {t.last_employee_message}
              </p>
            ) : (
              <p className="text-ink-400">אין הודעה אחרונה מהעובד/ת.</p>
            )}
            <p className="rounded-3xl rounded-ss-md bg-white px-4 py-2.5 leading-relaxed text-ink-700 ring-1 ring-ink-100">
              <strong className="font-semibold text-ink-900">יועץ:</strong> החישוב נעצר ונפתחה פנייה לנציג/ה.
            </p>
            <p className="num text-start text-[11px] text-ink-400">{timeOf(t.waiting_since)}</p>
          </div>
        </div>
      </div>

      <div className="card p-6">
        <h3 className="font-display text-lg font-semibold text-ink-900">תשובה לעובד</h3>

        {numeric && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {catalogValue !== null && (
              <button
                type="button"
                onClick={() => setParticipation(catalogValue)}
                className={`rounded-full px-4 py-1.5 text-sm transition ${
                  usingCatalog
                    ? "bg-mint-200 font-semibold text-ink-900 ring-2 ring-mint-300"
                    : "bg-mint-100 text-mint-700 hover:bg-mint-200"
                }`}
              >
                השתתפות לפי הקטלוג: <span className="num font-semibold">{ils(catalogCar!.default_participation)}</span>
              </button>
            )}
            <label className="flex items-center gap-2 rounded-full bg-white py-1 pe-1 ps-4 text-sm text-ink-600 ring-1 ring-ink-200 focus-within:ring-4 focus-within:ring-lilac-100">
              <span className="whitespace-nowrap">השתתפות אחרת (₪)</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                value={participation}
                onChange={(e) => setParticipation(e.target.value)}
                className="num w-24 rounded-full bg-lilac-50 px-3 py-1 font-display font-semibold text-ink-900 focus:outline-none"
                aria-label={`השתתפות מאושרת עבור ${who}`}
              />
            </label>
          </div>
        )}

        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={3}
          placeholder="למשל: אושרה השתתפות 350 ₪"
          className="mt-4 w-full resize-y rounded-2xl border border-ink-100 bg-ink-50 px-4 py-3 text-sm text-ink-900 transition placeholder:text-ink-400 focus:border-lilac-300 focus:bg-white focus:outline-none focus:ring-4 focus:ring-lilac-100"
          aria-label={`תשובה עבור ${who}`}
        />

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-ink-500">אחרי השליחה, היועץ ישלים את החישוב ויציג לעובד את הנטו.</p>
          <button
            type="button"
            disabled={!ready || sending}
            onClick={submit}
            className="rounded-full bg-lilac-600 px-7 py-3 font-display text-sm font-semibold text-white shadow-md shadow-lilac-400/40 transition hover:bg-lilac-700 disabled:opacity-40 disabled:shadow-none"
          >
            {sending ? "שולח…" : "שליחה וסגירת הפנייה"}
          </button>
        </div>
        {error && <p className="mt-2 text-xs text-blush-700">{error}</p>}
      </div>
    </div>
  );
}

/* ---------- login ---------- */

function RepLogin({ error, onSubmit }: { error: string | null; onSubmit: (key: string) => void }) {
  const [key, setKey] = useState("");
  return (
    <form
      className="card mx-auto mt-10 max-w-md space-y-4 p-7"
      onSubmit={(e) => {
        e.preventDefault();
        if (key.trim()) onSubmit(key.trim());
      }}
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-linear-to-br from-lilac-200 to-baby-200 text-xl">
        🔑
      </span>
      <div>
        <h2 className="font-display text-2xl font-semibold text-ink-900">כניסת נציג/ת שכר ותחבורה</h2>
        <p className="mt-1 text-sm text-ink-500">המסך מציג נתוני שכר של עובדים ולכן דורש מפתח נציג.</p>
      </div>
      <input
        type="password"
        autoComplete="off"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        aria-label="מפתח נציג"
        placeholder="מפתח נציג"
        className="num w-full rounded-2xl border border-ink-100 bg-ink-50 px-4 py-2.5 focus:border-lilac-300 focus:bg-white focus:outline-none focus:ring-4 focus:ring-lilac-100"
      />
      {error && (
        <p className="text-sm text-blush-700" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={!key.trim()}
        className="w-full rounded-full bg-lilac-600 px-4 py-2.5 font-display text-sm font-semibold text-white transition hover:bg-lilac-700 disabled:opacity-40"
      >
        כניסה
      </button>
    </form>
  );
}

/* ---------- screen ---------- */

export function RepDashboard({ onCountChange, onAuthChange }: Props) {
  const [hasKey, setHasKey] = useState(() => getRepKey() !== null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [last, setLast] = useState<RepAnswerResponse | null>(null);

  useEffect(() => onAuthChange?.(hasKey), [hasKey, onAuthChange]);

  const logout = useCallback(
    (reason: string | null) => {
      setRepKey(null);
      setHasKey(false);
      setTickets([]);
      setAuthError(reason);
      onCountChange?.(0);
    },
    [onCountChange],
  );

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
    setSelected(null);
    load();
  }

  // keep the selection on a ticket that still exists; default to the oldest one
  const current = tickets.find((t) => t.session_id === selected) ?? tickets[0] ?? null;

  return (
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.1fr)]">
      {/* RTL: the list is on the right */}
      <aside className="space-y-3 lg:sticky lg:top-24">
        <div className="flex items-end justify-between gap-2 px-1">
          <div>
            <h2 className="font-display text-2xl font-semibold text-ink-900">פניות ממתינות</h2>
            <p className="text-xs text-ink-500">
              <span className="num">{tickets.length}</span> פתוחות · מתעדכן כל{" "}
              <span className="num">{POLL_MS / 1000}</span> שניות
            </p>
          </div>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={load}
              className="rounded-full bg-white/80 px-3 py-1 text-xs text-ink-600 ring-1 ring-ink-100 transition hover:bg-white"
            >
              ↻ רענון
            </button>
            <button
              type="button"
              onClick={() => logout(null)}
              className="rounded-full bg-white/80 px-3 py-1 text-xs text-ink-600 ring-1 ring-ink-100 transition hover:bg-white"
            >
              יציאה
            </button>
          </div>
        </div>
        {tickets.map((t) => (
          <TicketCard
            key={t.session_id}
            t={t}
            active={current?.session_id === t.session_id}
            onSelect={() => setSelected(t.session_id)}
          />
        ))}
        {loaded && tickets.length === 0 && (
          <div className="rounded-3xl border-2 border-dashed border-lilac-200 bg-white/50 p-8 text-center text-sm text-ink-500">
            אין פניות ממתינות 🎉
          </div>
        )}
      </aside>

      <section className="min-w-0 space-y-4">
        {last && (
          <div className="rounded-3xl bg-mint-100 p-4 text-sm text-ink-900 ring-1 ring-mint-200" role="status">
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
        {error && <div className="rounded-3xl bg-blush-50 p-4 text-sm text-blush-700 ring-1 ring-blush-200">{error}</div>}

        {current ? (
          <TicketDetail key={current.session_id} t={current} onAnswered={answered} />
        ) : (
          loaded && (
            <div className="flex min-h-[20rem] items-center justify-center rounded-[2rem] border-2 border-dashed border-lilac-200 bg-white/50 p-8 text-center">
              <div>
                <p className="font-display text-3xl font-semibold text-ink-900">הכול מטופל ✨</p>
                <p className="mt-1 text-sm text-ink-500">פנייה חדשה תופיע כאן אוטומטית.</p>
              </div>
            </div>
          )
        )}
      </section>
    </div>
  );
}
