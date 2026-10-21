import type { Comparison, Options, Side, StateView } from "../api";
import { ils, num, signedIls, trend } from "../format";

interface Props {
  state: StateView | null;
  options: Options | null;
  busy: boolean;
  onSwitch: (choice: { car_id: string } | { waiver: true }) => void;
}

const TREND_STYLE = {
  up: { box: "border-emerald-300 bg-emerald-50", text: "text-emerald-700", arrow: "▲" },
  down: { box: "border-red-300 bg-red-50", text: "text-red-700", arrow: "▼" },
  flat: { box: "border-slate-200 bg-slate-50", text: "text-slate-700", arrow: "=" },
};

function BigNumber({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 text-center">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="num mt-1 text-3xl font-extrabold text-slate-900">{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-slate-500" title={sub}>{sub}</div>}
    </div>
  );
}

interface Row {
  label: string;
  today: string;
  alt: string;
  note?: string;
  strong?: boolean;
}

export function buildRows(c: Comparison): Row[] {
  const t: Side = c.today;
  const a: Side | null = c.alternative;
  const v = (f: (s: Side) => number) => (a ? ils(f(a)) : "—");
  const rows: Row[] = [
    { label: "רכב / סטטוס", today: t.label, alt: a ? a.label : c.alternative_label },
    { label: "שווי שימוש חודשי (למס)", today: ils(t.value_of_use), alt: v((s) => s.value_of_use) },
    { label: "דמי השתתפות עובד", today: ils(t.participation), alt: v((s) => s.participation) },
  ];
  if (t.is_electric || a?.is_electric) {
    rows.push({
      label: "החזר חשמל (ברוטו, מגולם)",
      today: ils(t.electric_gross),
      alt: v((s) => s.electric_gross),
      note: "העובד מקבל 250 ₪ נטו בדיוק; המעסיק מגלם את המס",
    });
  }
  if (t.maintenance_allowance || a?.maintenance_allowance) {
    rows.push({
      label: "אחזקת רכב בוויתור",
      today: ils(t.maintenance_allowance),
      alt: v((s) => s.maintenance_allowance),
      note: "חייבת במס, לא פנסיונית",
    });
  }
  if (t.family_gap_deduction || a?.family_gap_deduction) {
    rows.push({
      label: "חלק העובד בפער מסלול משפחתי",
      today: ils(t.family_gap_deduction),
      alt: v((s) => s.family_gap_deduction),
    });
  }
  rows.push(
    { label: "מס הכנסה", today: ils(t.income_tax), alt: v((s) => s.income_tax) },
    { label: "ביטוח לאומי ומס בריאות", today: ils(t.btl_health), alt: v((s) => s.btl_health) },
    { label: "נטו לחודש", today: ils(t.net), alt: v((s) => s.net), strong: true },
  );
  return rows;
}

function QuickSwitch({ state, options, busy, onSwitch }: Props) {
  if (!state || !options?.ok) return null;
  const current = state.target.target_action === "waiver" ? "waiver" : state.target.target_car_code ?? "";
  const ownCar = state.current.current_car_code;
  const familyCars = options.family_track.eligible ? options.family_track.cars : [];
  return (
    <label className="flex flex-wrap items-center gap-2 text-sm">
      <span className="font-semibold text-slate-700">החלפה מהירה:</span>
      <select
        value={current}
        disabled={busy || state.waiting_for_rep}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "waiver") onSwitch({ waiver: true });
          else if (v) onSwitch({ car_id: v });
        }}
        aria-label="בחירת חלופה מתוך הרכבים המותרים"
        className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 focus:border-teal-500 focus:outline-none"
      >
        <option value="" disabled>
          בחר/י רכב מהקטלוג המורשה…
        </option>
        <optgroup label={`רכבים מותרים (קבוצה ${options.group} ומטה)`}>
          {options.allowed_cars
            .filter((c) => c.car_id !== ownCar)
            .map((c) => (
              <option key={c.car_id} value={c.car_id}>
                {c.model_name} · קבוצה {c.group} · {c.drive} · שווי שימוש {num(c.value_of_use)} ₪
              </option>
            ))}
        </optgroup>
        {familyCars.length > 0 && (
          <optgroup label="מסלול ברוכת ילדים">
            {familyCars.map((c) => (
              <option key={c.car_id} value={c.car_id}>
                {c.model_name} · שווי שימוש {num(c.value_of_use)} ₪
              </option>
            ))}
          </optgroup>
        )}
        {options.waiver.allowed && state.current.status !== "waiver" && (
          <option value="waiver">ויתור על הרכב (אחזקה {num(options.waiver.maintenance)} ₪)</option>
        )}
      </select>
    </label>
  );
}

function ProfileSummary({ state }: { state: StateView }) {
  const p = state.profile;
  // [label, value, field name in missing_info]
  const facts: [string, string | null, string][] = [
    ["קבוצה", p.eligibility_group, "eligibility_group"],
    ["עיסוק", p.occupation, "occupation"],
    ["שכר בסיס", p.base_salary !== null ? ils(p.base_salary) : null, "base_salary"],
    ["עמלות/בונוס", p.commissions_bonuses !== null ? ils(p.commissions_bonuses) : null, "commissions_bonuses"],
    ["פנסיה", p.pension_pct !== null ? `${p.pension_pct}%` : null, "pension_pct"],
    ["השתלמות", p.hishtalmut_pct !== null ? `${p.hishtalmut_pct}%` : null, "hishtalmut_pct"],
    ["נקודות זיכוי", p.credit_points !== null ? String(p.credit_points) : null, "credit_points"],
    ["ילדים עד 23", p.children_count !== null ? String(p.children_count) : null, "children_count"],
  ];
  // empty + still needed = "חסר"; empty + not needed for this calculation = "לא נדרש"
  const blocked = state.precheck?.eligible === false;
  const emptyLabel = (field: string) =>
    blocked || state.missing_info.includes(field) ? "חסר" : "לא נדרש";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="mb-2 text-sm font-semibold text-slate-600">
        הנתונים שנאספו{state.display_name ? ` · ${state.display_name}` : ""}
      </h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        {facts.map(([k, val, field]) => (
          <div key={k}>
            <dt className="text-xs text-slate-500">{k}</dt>
            <dd className={val ? "num text-start font-medium" : "text-slate-400"}>
              {val ?? emptyLabel(field)}
            </dd>
          </div>
        ))}
      </dl>
      {state.missing_labels.length > 0 && (
        <p className="mt-3 text-xs text-slate-600">
          <span className="font-semibold">עוד חסר לחישוב:</span> {state.missing_labels.join(" · ")}
        </p>
      )}
    </div>
  );
}

export function ComparisonDashboard(props: Props) {
  const { state } = props;
  const c = state?.last_comparison ?? null;

  if (!state) {
    return (
      <section className="flex min-h-[20rem] items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-white p-8 text-center text-slate-500">
        <div>
          <p className="text-lg font-semibold text-slate-700">כאן תופיע ההשוואה</p>
          <p className="mt-1 text-sm">נטו היום מול נטו בחלופה, ההפרש והפירוט המלא - מתעדכן אוטומטית.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-4" aria-label="השוואת נטו">
      {c && c.is_eligible && c.net_alt !== null ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <BigNumber label="נטו היום" value={ils(c.net_today)} sub={c.current_label} />
            <BigNumber label="נטו בחלופה" value={ils(c.net_alt)} sub={c.alternative_label} />
            {(() => {
              const s = TREND_STYLE[trend(c.net_diff)];
              return (
                <div className={`rounded-xl border-2 p-4 text-center ${s.box}`} data-testid="net-diff">
                  <div className="text-sm text-slate-600">הפרש חודשי בנטו</div>
                  <div className={`num mt-1 text-3xl font-extrabold ${s.text}`}>
                    {s.arrow} {signedIls(c.net_diff)}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {c.net_diff && c.net_diff > 0 ? "תרוויח/י בכל חודש" : c.net_diff ? "תפסיד/י בכל חודש" : "ללא שינוי"}
                  </div>
                </div>
              );
            })()}
          </div>

          <div className="rounded-xl border border-teal-200 bg-teal-50/60 p-4">
            <h3 className="text-sm font-semibold text-teal-900">ממה נובע ההפרש?</h3>
            <p className="mt-1 leading-relaxed text-teal-950">{c.explanation}</p>
            <p className="mt-1 text-xs text-teal-800">{c.reason}</p>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-start font-semibold">רכיב</th>
                  <th className="px-3 py-2 text-start font-semibold">היום</th>
                  <th className="px-3 py-2 text-start font-semibold">בחלופה</th>
                </tr>
              </thead>
              <tbody>
                {buildRows(c).map((r) => (
                  <tr key={r.label} className={`border-t border-slate-100 ${r.strong ? "bg-slate-50 font-bold" : ""}`}>
                    <td className="px-3 py-2">
                      {r.label}
                      {r.note && <div className="text-[11px] font-normal text-slate-500">{r.note}</div>}
                    </td>
                    <td className="num px-3 py-2 text-start">{r.today}</td>
                    <td className="num px-3 py-2 text-start">{r.alt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : c && !c.is_eligible ? (
        <div className="rounded-xl border-2 border-red-300 bg-red-50 p-4" role="alert">
          <h3 className="font-semibold text-red-800">החלופה המבוקשת אינה אפשרית</h3>
          <p className="mt-1 text-red-900">{c.reason}</p>
          <p className="mt-2 text-sm text-red-800">
            נטו היום: <span className="num font-semibold">{ils(c.net_today)}</span> · אפשר לבחור חלופה מותרת למטה.
          </p>
        </div>
      ) : state.precheck?.eligible === false ? (
        <div className="rounded-xl border-2 border-red-300 bg-red-50 p-4" role="alert" data-testid="blocked">
          <h3 className="font-semibold text-red-800">הבקשה אינה אפשרית: {state.precheck.requested}</h3>
          <p className="mt-1 text-red-900">{state.precheck.reason}</p>
          {state.precheck.alternatives && state.precheck.alternatives.length > 0 && (
            <>
              <p className="mt-3 text-sm font-semibold text-red-900">חלופות מותרות - לחצו לבדיקה:</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {state.precheck.alternatives.map((a) => (
                  <button
                    key={a.car_id}
                    type="button"
                    disabled={props.busy}
                    onClick={() => props.onSwitch({ car_id: a.car_id })}
                    className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-start text-sm hover:border-teal-500 disabled:opacity-50"
                  >
                    <span className="font-semibold">{a.model_name}</span>
                    <span className="block text-xs text-slate-500">
                      קבוצה {a.group} · {a.drive} · שווי שימוש <span className="num">{num(a.value_of_use)} ₪</span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
          ההשוואה תופיע ברגע שיהיו כל הנתונים וחלופה מבוקשת.
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <QuickSwitch {...props} />
        {!props.options?.ok && (
          <p className="text-xs text-slate-500">
            החלפה מהירה תתאפשר אחרי שתיקבע קבוצת הזכאות (או העיסוק).
          </p>
        )}
      </div>

      <ProfileSummary state={state} />
    </section>
  );
}
