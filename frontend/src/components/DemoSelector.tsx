import type { DemoEmployee } from "../api";

interface Props {
  employees: DemoEmployee[];
  activeName: string | null;
  busy: boolean;
  onLoad: (empId: string) => void;
  onNew: () => void;
}

export function DemoSelector({ employees, activeName, busy, onLoad, onNew }: Props) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-600">עובדי הדגמה - טעינה בלחיצה</h2>
        <button
          type="button"
          onClick={onNew}
          disabled={busy}
          className="rounded-lg border border-slate-300 px-3 py-1 text-sm font-medium hover:bg-slate-100 disabled:opacity-50"
        >
          + שיחה חדשה
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {employees.map((e) => {
          const active = activeName?.includes(e.employee_id);
          return (
            <button
              key={e.employee_id}
              type="button"
              title={e.question}
              disabled={busy}
              onClick={() => onLoad(e.employee_id)}
              className={`rounded-lg border px-3 py-1.5 text-start text-sm transition disabled:opacity-50 ${
                active
                  ? "border-teal-600 bg-teal-50 text-teal-900"
                  : "border-slate-200 bg-slate-50 hover:border-teal-400 hover:bg-white"
              }`}
            >
              <span className="font-semibold">{e.name}</span>{" "}
              <span className="num text-xs text-slate-500">{e.employee_id}</span>
              <span className="block text-xs text-slate-500">{e.occupation}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
