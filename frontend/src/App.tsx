import { useCallback, useEffect, useState } from "react";
import { api, API_BASE, ApiError, getRepKey, type DemoEmployee } from "./api";
import { EmployeeScreen } from "./components/EmployeeScreen";
import { RepDashboard } from "./components/RepDashboard";

type Tab = "employee" | "rep";

const CONNECT_RETRIES = 15; // x 5s = up to ~75s, enough for a cold start
const CONNECT_RETRY_MS = 5000;
const WAKING_STATUSES = [0, 502, 503, 504]; // network error / gateway while the server wakes

function Logo() {
  return (
    <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-linear-to-br from-lilac-300 via-blush-200 to-peach-200 text-ink-900 shadow-md shadow-lilac-300/40">
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M5 16V11.5L7 7h10l2 4.5V16" strokeLinejoin="round" />
        <path d="M3.5 16h17" strokeLinecap="round" />
        <circle cx="7.5" cy="16.5" r="1.6" fill="currentColor" stroke="none" />
        <circle cx="16.5" cy="16.5" r="1.6" fill="currentColor" stroke="none" />
      </svg>
    </span>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("employee");
  const [demo, setDemo] = useState<DemoEmployee[]>([]);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [attempt, setAttempt] = useState(0); // bump to retry the connection
  const [ticketCount, setTicketCount] = useState(0);
  const [repAuthed, setRepAuthed] = useState(() => getRepKey() !== null);
  const onCount = useCallback((n: number) => setTicketCount(n), []);

  // A cloud server on a free plan sleeps when idle; the first request can fail or hang
  // while it wakes up (~30-60s). Retry quietly before showing an error.
  useEffect(() => {
    let cancelled = false;
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    // a waking server may also just hold the request: show the notice if it is slow
    const slow = setTimeout(() => !cancelled && setConnecting(true), 1500);
    (async () => {
      setBackendError(null);
      for (let i = 0; i < CONNECT_RETRIES; i++) {
        try {
          const c = await api.catalog();
          if (!cancelled) setDemo(c.demo_employees);
          break;
        } catch (e) {
          const waking = e instanceof ApiError && WAKING_STATUSES.includes(e.status);
          if (cancelled) return;
          if (!waking || i === CONNECT_RETRIES - 1) {
            setBackendError(e instanceof ApiError ? e.message : String(e));
            break;
          }
          setConnecting(true);
          await sleep(CONNECT_RETRY_MS);
          if (cancelled) return;
        }
      }
      clearTimeout(slow);
      if (!cancelled) setConnecting(false);
    })();
    return () => {
      cancelled = true;
      clearTimeout(slow);
    };
  }, [attempt]);

  // keep the rep badge fresh while on the employee tab (only once a rep key was entered)
  useEffect(() => {
    if (tab === "rep") return;
    const poll = () => {
      if (!getRepKey()) return;
      api
        .tickets()
        .then((t) => setTicketCount(t.length))
        .catch(() => setTicketCount(0));
    };
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, [tab]);

  const tabClass = (t: Tab) =>
    `flex items-center gap-2 rounded-full px-5 py-2 font-display text-sm font-medium transition ${
      tab === t ? "bg-lilac-200 text-ink-900 shadow-sm" : "text-ink-500 hover:bg-lilac-50 hover:text-ink-900"
    }`;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-white/80 bg-white/60 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-3">
          <div className="flex items-center gap-3">
            <Logo />
            <div>
              <h1 className="font-display text-xl font-semibold leading-tight text-ink-900">יועץ הרכב</h1>
              <p className="text-xs text-ink-500">
                {tab === "employee" ? "רכב צמוד או ויתור - מה זה עושה לנטו שלך" : "עמדת נציג/ת שכר"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {tab === "rep" && repAuthed && (
              <span className="hidden items-center gap-2 rounded-full bg-mint-100 px-3 py-1 text-xs text-mint-700 sm:flex">
                <span className="h-1.5 w-1.5 rounded-full bg-mint-500" />
                מחובר/ת עם מפתח נציג
              </span>
            )}
            <nav className="flex gap-1 rounded-full bg-white/80 p-1 shadow-sm ring-1 ring-ink-100" role="tablist">
              <button
                role="tab"
                aria-selected={tab === "employee"}
                aria-label="מסך עובד"
                className={tabClass("employee")}
                onClick={() => setTab("employee")}
              >
                עובד/ת
              </button>
              <button
                role="tab"
                aria-selected={tab === "rep"}
                aria-label={ticketCount > 0 ? `מסך נציג (${ticketCount} פניות)` : "מסך נציג"}
                className={tabClass("rep")}
                onClick={() => setTab("rep")}
              >
                נציג/ה
                {ticketCount > 0 && (
                  <span className="num flex h-5 min-w-5 items-center justify-center rounded-full bg-blush-500 px-1.5 text-[11px] font-bold text-white">
                    {ticketCount}
                  </span>
                )}
              </button>
            </nav>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-6">
        {connecting && !backendError && (
          <div className="mb-4 flex items-center gap-3 rounded-3xl border border-lilac-200 bg-lilac-50 p-4 text-sm text-ink-700" role="status">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-lilac-300 border-t-transparent" aria-hidden="true" />
            מתחברים לשרת... בפתיחה הראשונה זה יכול לקחת עד דקה.
          </div>
        )}
        {backendError && (
          <div className="mb-4 rounded-3xl border border-blush-200 bg-blush-50 p-4 text-sm text-blush-700" role="alert">
            {import.meta.env.DEV ? (
              <>
                לא ניתן להתחבר לשרת ב-<span className="num">{API_BASE}</span>: {backendError}
                <br />
                הפעילו את ה-backend: <code className="num">uvicorn backend.main:app --reload --port 8000</code>
              </>
            ) : (
              <>לא הצלחנו להתחבר לשירות כרגע. {backendError}</>
            )}
            <div className="mt-3">
              <button
                type="button"
                className="rounded-full bg-white px-4 py-1.5 font-medium text-blush-700 shadow-sm ring-1 ring-blush-200 hover:bg-blush-100"
                onClick={() => setAttempt((n) => n + 1)}
              >
                נסו שוב
              </button>
            </div>
          </div>
        )}
        {/* both screens stay mounted so switching tabs keeps the conversation */}
        <div hidden={tab !== "employee"}>
          <EmployeeScreen demoEmployees={demo} />
        </div>
        <div hidden={tab !== "rep"}>
          {tab === "rep" && <RepDashboard onCountChange={onCount} onAuthChange={setRepAuthed} />}
        </div>
      </main>
    </div>
  );
}
