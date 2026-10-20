import { useCallback, useEffect, useState } from "react";
import { api, API_BASE, ApiError, getRepKey, type DemoEmployee } from "./api";
import { EmployeeScreen } from "./components/EmployeeScreen";
import { RepDashboard } from "./components/RepDashboard";

type Tab = "employee" | "rep";

export default function App() {
  const [tab, setTab] = useState<Tab>("employee");
  const [demo, setDemo] = useState<DemoEmployee[]>([]);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [ticketCount, setTicketCount] = useState(0);
  const onCount = useCallback((n: number) => setTicketCount(n), []);

  useEffect(() => {
    api
      .catalog()
      .then((c) => setDemo(c.demo_employees))
      .catch((e) => setBackendError(e instanceof ApiError ? e.message : String(e)));
  }, []);

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
    `rounded-lg px-4 py-2 text-sm font-semibold transition ${
      tab === t ? "bg-white text-teal-800 shadow" : "text-teal-50 hover:bg-teal-600"
    }`;

  return (
    <div className="min-h-screen">
      <header className="bg-teal-700 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <h1 className="text-xl font-extrabold">יועץ הרכב</h1>
            <p className="text-xs text-teal-100">כמה תשנה בחירת הרכב את הנטו שלך - חישוב דטרמיניסטי לפי כללי החברה</p>
          </div>
          <nav className="flex gap-1 rounded-xl bg-teal-800/60 p-1" role="tablist">
            <button role="tab" aria-selected={tab === "employee"} className={tabClass("employee")} onClick={() => setTab("employee")}>
              מסך עובד
            </button>
            <button role="tab" aria-selected={tab === "rep"} className={tabClass("rep")} onClick={() => setTab("rep")}>
              מסך נציג
              {ticketCount > 0 && (
                <span className="num ms-2 rounded-full bg-amber-400 px-2 py-0.5 text-xs text-amber-950">{ticketCount}</span>
              )}
            </button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-4">
        {backendError && (
          <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
            לא ניתן להתחבר לשרת ב-<span className="num">{API_BASE}</span>: {backendError}
            <br />
            הפעילו את ה-backend: <code className="num">uvicorn backend.main:app --reload --port 8000</code>
          </div>
        )}
        {/* both screens stay mounted so switching tabs keeps the conversation */}
        <div hidden={tab !== "employee"}>
          <EmployeeScreen demoEmployees={demo} />
        </div>
        <div hidden={tab !== "rep"}>{tab === "rep" && <RepDashboard onCountChange={onCount} />}</div>
      </main>
    </div>
  );
}
