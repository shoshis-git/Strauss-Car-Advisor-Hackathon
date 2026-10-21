import { useEffect, useState } from "react";
import { api, type DemoEmployee, type Options } from "../api";
import { useEmployeeSession } from "../useEmployeeSession";
import { ChatPanel } from "./ChatPanel";
import { ComparisonDashboard } from "./ComparisonDashboard";
import { DemoSelector } from "./DemoSelector";

export function EmployeeScreen({ demoEmployees }: { demoEmployees: DemoEmployee[] }) {
  const s = useEmployeeSession();
  const [options, setOptions] = useState<Options | null>(null);
  const group = s.state?.profile.eligibility_group ?? null;
  const children = s.state?.profile.children_count ?? null;
  const occupation = s.state?.profile.occupation ?? null;

  // Allowed cars depend on group / children / occupation - refetch when they change.
  useEffect(() => {
    if (!s.sessionId || !group) {
      setOptions(null);
      return;
    }
    let cancelled = false;
    api
      .options(s.sessionId)
      .then((o) => !cancelled && setOptions(o))
      .catch(() => !cancelled && setOptions(null));
    return () => {
      cancelled = true;
    };
  }, [s.sessionId, group, children, occupation]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* RTL: the first column is on the right */}
      <div className="flex min-w-0 flex-col gap-4">
        <DemoSelector
          employees={demoEmployees}
          activeName={s.state?.display_name ?? null}
          busy={s.busy}
          onLoad={s.loadDemo}
          onNew={s.reset}
        />
        <ChatPanel
          items={s.items}
          state={s.state}
          busy={s.busy}
          error={s.error}
          notice={s.notice}
          onSend={s.send}
          onUpload={(name) =>
            s.addLocalNote(
              `📎 הועלה הקובץ "${name}". בגרסת הפיילוט התלוש/החוזה ייקרא אוטומטית וימלא את הנתונים.`,
            )
          }
        />
      </div>
      <div className="min-w-0">
        <ComparisonDashboard state={s.state} options={options} busy={s.busy} onSwitch={s.setAlternative} />
      </div>
    </div>
  );
}
