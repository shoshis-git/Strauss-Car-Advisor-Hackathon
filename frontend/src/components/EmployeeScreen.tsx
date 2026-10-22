import { useEffect, useState } from "react";
import { api, type DemoEmployee, type Options, type Ranking } from "../api";
import { useEmployeeSession } from "../useEmployeeSession";
import { ChatPanel } from "./ChatPanel";
import { ComparisonDashboard } from "./ComparisonDashboard";
import { DemoSelector } from "./DemoSelector";
import { PayslipReview } from "./PayslipReview";

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

  // All my options: recomputed whenever the inputs it depends on change (pay data,
  // today's situation, and the selected alternative - to mark it in the list).
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const rankingKey = s.state
    ? JSON.stringify([s.state.profile, s.state.current, s.state.target.target_action, s.state.target.target_car_code])
    : null;
  useEffect(() => {
    if (!s.sessionId || !rankingKey) {
      setRanking(null);
      return;
    }
    let cancelled = false;
    api
      .ranking(s.sessionId)
      .then((r) => !cancelled && setRanking(r))
      .catch(() => !cancelled && setRanking(null));
    return () => {
      cancelled = true;
    };
  }, [s.sessionId, rankingKey]);

  const name = s.state?.display_name ?? null;
  const subtitle = [occupation, group ? `קבוצה ${group}` : null].filter(Boolean).join(" · ");

  return (
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.85fr)]">
      {/* RTL: the first column (chat) is on the right */}
      <div className="flex min-w-0 flex-col lg:sticky lg:top-24 lg:h-[calc(100vh-8rem)]">
        <ChatPanel
          items={s.messages}
          state={s.state}
          busy={s.busy}
          error={s.error}
          notice={s.notice}
          onSend={s.send}
          onUpload={s.uploadPayslip}
          header={
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-linear-to-br from-peach-200 to-blush-200 font-display text-base font-semibold text-ink-900">
                    {name ? name.trim().charAt(0) : "?"}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-display text-base font-semibold text-ink-900">{name ?? "שיחה חדשה"}</div>
                    <div className="truncate text-xs text-ink-500">{subtitle || "ספר/י לי על התפקיד והרכב"}</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={s.reset}
                  disabled={s.busy}
                  className="shrink-0 rounded-full bg-white px-4 py-1.5 text-xs font-semibold text-ink-700 ring-1 ring-ink-100 transition hover:bg-lilac-50 hover:ring-lilac-200 disabled:opacity-50"
                >
                  שיחה חדשה
                </button>
              </div>
              <DemoSelector employees={demoEmployees} activeName={name} busy={s.busy} onLoad={s.loadDemo} />
            </div>
          }
          review={
            s.payslip && (
              <PayslipReview
                extraction={s.payslip}
                busy={s.busy}
                onConfirm={s.confirmPayslip}
                onCancel={s.cancelPayslip}
                onReadImage={s.readPayslipImage}
              />
            )
          }
        />
      </div>
      <div className="min-w-0">
        <ComparisonDashboard
          state={s.state}
          options={options}
          ranking={ranking}
          busy={s.busy}
          onSwitch={s.setAlternative}
        />
      </div>
    </div>
  );
}
