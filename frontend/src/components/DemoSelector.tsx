import type { DemoEmployee } from "../api";

interface Props {
  employees: DemoEmployee[];
  activeName: string | null;
  busy: boolean;
  onLoad: (empId: string) => void;
}

/** Compact strip of demo employees, shown inside the chat header. */
export function DemoSelector({ employees, activeName, busy, onLoad }: Props) {
  if (employees.length === 0) return null;
  return (
    <div className="scroll-thin flex items-center gap-2 overflow-x-auto pb-0.5">
      <span className="shrink-0 text-[11px] font-medium text-ink-400">הדגמה:</span>
      {employees.map((e) => {
        const active = activeName?.includes(e.employee_id);
        return (
          <button
            key={e.employee_id}
            type="button"
            title={`${e.occupation} · ${e.question}`}
            disabled={busy}
            onClick={() => onLoad(e.employee_id)}
            className={`shrink-0 rounded-full px-3 py-1 text-xs transition disabled:opacity-50 ${
              active
                ? "bg-lilac-200 font-semibold text-ink-900 shadow-sm"
                : "bg-white text-ink-600 ring-1 ring-ink-100 hover:bg-lilac-50 hover:ring-lilac-200"
            }`}
          >
            <span className="font-semibold">{e.name}</span>{" "}
            <span className="num text-[10px] text-ink-400">{e.employee_id}</span>
          </button>
        );
      })}
    </div>
  );
}
