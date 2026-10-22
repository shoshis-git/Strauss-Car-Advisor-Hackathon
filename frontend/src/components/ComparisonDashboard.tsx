import type { CarSummary, Comparison, Options, Ranking, Side, StateView } from "../api";
import { ils, num, signedIls, trend, type Trend } from "../format";
import { AllOptions } from "./AllOptions";

interface Props {
  state: StateView | null;
  options: Options | null;
  busy: boolean;
  onSwitch: (choice: { car_id: string } | { waiver: true }) => void;
  /** all my options (every permitted alternative, ranked) - optional */
  ranking?: Ranking | null;
  /** the ranking is shown (after a calculated answer, or once asked for) */
  rankingWanted?: boolean;
  onRequestRanking?: () => void;
}

const TREND_TEXT: Record<Trend, string> = {
  up: "text-mint-700",
  down: "text-blush-700",
  flat: "text-ink-700",
};

/** soft pastel wash behind the bottom line */
const TREND_WASH: Record<Trend, string> = {
  up: "from-mint-100 via-baby-50 to-lilac-100",
  down: "from-blush-100 via-peach-50 to-butter-50",
  flat: "from-ink-50 via-white to-lilac-50",
};

const IMPACT_PILL: Record<Trend, string> = {
  up: "bg-mint-100 text-mint-700",
  down: "bg-blush-100 text-blush-700",
  flat: "text-ink-300",
};

interface Impact {
  text: string;
  dir: Trend;
}

interface Row {
  label: string;
  today: string;
  alt: string;
  note?: string;
  strong?: boolean;
  /** effect of this line on the monthly net (alternative vs today) */
  impact?: Impact;
}

/** d > 0 = the alternative leaves more money in the employee's pocket */
function moneyImpact(d: number): Impact {
  if (!d) return { text: "—", dir: "flat" };
  return d > 0 ? { text: `▲ ${ils(d)}`, dir: "up" } : { text: `▼ ${ils(-d)}`, dir: "down" };
}

/** say where the new car's participation comes from - a documented value, never a guess */
const PARTICIPATION_SOURCE: Record<string, string | undefined> = {
  company_catalogue_default: "בחלופה: לפי קטלוג הרכבים של החברה. סוכם אחרת? כתבו ביועץ",
  approved_by_representative: "בחלופה: אושר על ידי נציג/ת שכר",
};

export function buildRows(c: Comparison): Row[] {
  const t: Side = c.today;
  const a: Side | null = c.alternative;
  const v = (f: (s: Side) => number) => (a ? ils(f(a)) : "—");
  const imp = (d: (alt: Side) => number) => (a ? moneyImpact(d(a)) : undefined);
  const rows: Row[] = [
    { label: "רכב / סטטוס", today: t.label, alt: a ? a.label : c.alternative_label },
    {
      label: "שווי שימוש חודשי (למס)",
      today: ils(t.value_of_use),
      alt: v((s) => s.value_of_use),
      impact: a
        ? a.value_of_use < t.value_of_use
          ? { text: "▲ פחות מס", dir: "up" }
          : a.value_of_use > t.value_of_use
            ? { text: "▼ יותר מס", dir: "down" }
            : { text: "—", dir: "flat" }
        : undefined,
    },
    {
      label: "דמי השתתפות עובד",
      today: ils(t.participation),
      alt: v((s) => s.participation),
      note: PARTICIPATION_SOURCE[c.alternative_participation_source ?? ""],
      impact: imp((s) => t.participation - s.participation),
    },
  ];
  if (t.is_electric || a?.is_electric) {
    rows.push({
      label: "החזר חשמל (ברוטו, מגולם)",
      today: ils(t.electric_gross),
      alt: v((s) => s.electric_gross),
      note: "העובד מקבל 250 ₪ נטו בדיוק; המעסיק מגלם את המס",
      impact: imp((s) => s.electric_gross - t.electric_gross),
    });
  }
  if (t.maintenance_allowance || a?.maintenance_allowance) {
    rows.push({
      label: "אחזקת רכב בוויתור",
      today: ils(t.maintenance_allowance),
      alt: v((s) => s.maintenance_allowance),
      note: "חייבת במס, לא פנסיונית",
      impact: imp((s) => s.maintenance_allowance - t.maintenance_allowance),
    });
  }
  if (t.family_gap_deduction || a?.family_gap_deduction) {
    rows.push({
      label: "חלק העובד בפער מסלול משפחתי",
      today: ils(t.family_gap_deduction),
      alt: v((s) => s.family_gap_deduction),
      impact: imp((s) => t.family_gap_deduction - s.family_gap_deduction),
    });
  }
  rows.push(
    { label: "מס הכנסה", today: ils(t.income_tax), alt: v((s) => s.income_tax), impact: imp((s) => t.income_tax - s.income_tax) },
    {
      label: "ביטוח לאומי ומס בריאות",
      today: ils(t.btl_health),
      alt: v((s) => s.btl_health),
      impact: imp((s) => t.btl_health - s.btl_health),
    },
    {
      label: "נטו לחודש",
      today: ils(t.net),
      alt: v((s) => s.net),
      strong: true,
      impact: a ? { text: signedIls(a.net - t.net), dir: trend(a.net - t.net) } : undefined,
    },
  );
  return rows;
}

/* ---------- hero: the bottom line ---------- */

function SalaryBars({ c }: { c: Comparison }) {
  const a = c.alternative!;
  const sides: { label: string; side: Side; netColor: string }[] = [
    { label: `היום · ${c.today.label}`, side: c.today, netColor: "bg-baby-300" },
    { label: `בחלופה · ${a.label}`, side: a, netColor: "bg-lilac-400" },
  ];
  const total = (s: Side) => s.net + s.income_tax + s.btl_health;
  const max = Math.max(...sides.map((x) => total(x.side)), 1);
  const pct = (n: number) => `${Math.max(0, (n / max) * 100)}%`;
  return (
    <div className="flex flex-col justify-center gap-4 rounded-3xl bg-white/70 p-5 ring-1 ring-white">
      <h3 className="font-display text-sm font-medium text-ink-500">לאן הולך השכר שלך</h3>
      {sides.map(({ label, side, netColor }) => (
        <div key={label}>
          <div className="mb-1.5 flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate font-medium text-ink-700" title={label}>
              {label}
            </span>
            <span className="shrink-0 text-ink-500">
              <span className="num font-display text-sm font-semibold text-ink-900">{ils(side.net)}</span> נטו
            </span>
          </div>
          <div className="flex h-3.5 gap-0.5 overflow-hidden rounded-full bg-ink-50">
            <span className={`${netColor} h-full rounded-full`} style={{ width: pct(side.net) }} />
            <span className="h-full rounded-full bg-peach-300" style={{ width: pct(side.income_tax) }} />
            <span className="h-full rounded-full bg-blush-200" style={{ width: pct(side.btl_health) }} />
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-lilac-400" /> נטו
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-peach-300" /> מס הכנסה
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-blush-200" /> ביטוח לאומי ובריאות
        </span>
      </div>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M4 10.5l4 4 8-9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Hero({ c }: { c: Comparison }) {
  const diff = c.net_diff ?? 0;
  const dir = trend(diff);
  const a = c.alternative;
  return (
    <div
      className={`grid grid-cols-1 gap-6 rounded-[2rem] bg-linear-to-br p-6 shadow-[0_16px_40px_-18px_rgb(110_86_180/0.35)] ring-1 ring-white md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] ${TREND_WASH[dir]}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="chip bg-white/80 font-semibold text-ink-700">{c.current_label}</span>
          <span className="text-ink-400" aria-hidden="true">←</span>
          <span className="chip bg-lilac-200 font-semibold text-ink-900">
            {c.alternative_label}
            {a?.is_electric ? " · ⚡ חשמלי" : ""}
          </span>
        </div>
        <p className="mt-5 font-display text-base text-ink-600">
          {diff > 0 ? "תרוויח/י בכל חודש" : diff < 0 ? "תפסיד/י בכל חודש" : "ללא שינוי בנטו"}
        </p>
        <div
          data-testid="net-diff"
          className={`num mt-1 text-start font-display font-semibold leading-none tracking-tight ${TREND_TEXT[dir]}`}
        >
          <span className="text-6xl sm:text-7xl">{signedIls(diff).replace(/ ₪$/, "")}</span>
          <span className="ms-1 text-3xl font-medium sm:text-4xl"> ₪</span>
        </div>
        <p className="mt-3 text-sm text-ink-500">
          {diff !== 0 && (
            <>
              כ-<span className="num">{ils(Math.abs(diff * 12))}</span> בשנה ·{" "}
            </>
          )}
          נטו, אחרי מס וביטוח לאומי
        </p>
        <p className="mt-5 inline-flex items-center gap-1.5 rounded-full bg-white/80 px-3.5 py-1.5 text-xs font-medium text-mint-700">
          <CheckIcon />
          {c.reason}
        </p>
      </div>
      <SalaryBars c={c} />
    </div>
  );
}

function Breakdown({ c }: { c: Comparison }) {
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-2 px-6 pb-4 pt-5">
        <div className="min-w-0">
          <h3 className="font-display text-xl font-semibold text-ink-900">פירוט החישוב</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-500">{c.explanation}</p>
        </div>
        <span className="chip bg-baby-100 text-baby-700">שווי שימוש לפי מאגר רשות המסים 2026</span>
      </div>
      <div className="overflow-x-auto px-3 pb-3">
        <table className="w-full text-sm">
          <thead className="text-xs text-ink-400">
            <tr>
              <th className="px-3 py-2 text-start font-medium">רכיב</th>
              <th className="px-3 py-2 text-start font-medium">היום</th>
              <th className="px-3 py-2 text-start font-medium">בחלופה</th>
              <th className="px-3 py-2 text-end font-medium">השפעה על הנטו</th>
            </tr>
          </thead>
          <tbody>
            {buildRows(c).map((r) => (
              <tr
                key={r.label}
                className={r.strong ? "bg-lilac-100/70 font-display font-semibold text-ink-900" : "text-ink-700 even:bg-ink-50/60"}
              >
                <td className={`px-3 py-3 ${r.strong ? "rounded-s-2xl" : "rounded-s-xl"}`}>
                  {r.label}
                  {r.note && <div className="font-sans text-[11px] font-normal text-ink-400">{r.note}</div>}
                </td>
                <td className="num px-3 py-3 text-start">{r.today}</td>
                <td className="num px-3 py-3 text-start">{r.alt}</td>
                <td className={`px-3 py-3 text-end ${r.strong ? "rounded-e-2xl" : "rounded-e-xl"}`}>
                  {r.impact && (
                    <span className={`num inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${IMPACT_PILL[r.impact.dir]}`}>
                      {r.impact.text}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------- alternatives ---------- */

function QuickSwitch({ state, options, busy, onSwitch }: Props) {
  if (!state || !options?.ok) return null;
  const current = state.target.target_action === "waiver" ? "waiver" : state.target.target_car_code ?? "";
  const ownCar = state.current.current_car_code;
  const familyCars = options.family_track.eligible ? options.family_track.cars : [];
  return (
    <label className="block text-xs">
      <span className="mb-1 block font-medium text-ink-600">כל הרכבים המותרים</span>
      <select
        value={current}
        disabled={busy || state.waiting_for_rep}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "waiver") onSwitch({ waiver: true });
          else if (v) onSwitch({ car_id: v });
        }}
        aria-label="בחירת חלופה מתוך הרכבים המותרים"
        className="w-full rounded-2xl border border-white bg-white/90 px-3 py-2 text-sm text-ink-900 focus:border-lilac-300 focus:outline-none focus:ring-4 focus:ring-lilac-100 disabled:opacity-50"
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

function Alternatives(props: Props) {
  const { state, options, busy, onSwitch } = props;
  const ready = !!state && !!options?.ok;
  const exclude = new Set([state?.current.current_car_code, state?.target.target_car_code]);
  const picks: CarSummary[] = ready
    ? options!.allowed_cars
        .filter((c) => !exclude.has(c.car_id) && c.group === options!.group)
        .sort((x, y) => x.value_of_use - y.value_of_use)
        .slice(0, 3)
    : [];
  const canWaive =
    ready && options!.waiver.allowed && state!.current.status !== "waiver" && state!.target.target_action !== "waiver";
  const locked = busy || !!state?.waiting_for_rep;

  return (
    <div className="flex flex-col gap-3 rounded-[1.75rem] bg-linear-to-br from-peach-100 via-butter-50 to-blush-50 p-5 shadow-[0_12px_32px_-14px_rgb(200_120_80/0.3)] ring-1 ring-white">
      <h3 className="font-display text-lg font-semibold text-ink-900">
        {ready ? `עוד חלופות בקבוצה ${options!.group}` : "חלופות נוספות"}
      </h3>
      {!ready && (
        <p className="text-xs leading-relaxed text-ink-600">
          החלפה מהירה תתאפשר אחרי שתיקבע קבוצת הזכאות (או העיסוק).
        </p>
      )}
      {picks.map((c) => (
        <button
          key={c.car_id}
          type="button"
          disabled={locked}
          onClick={() => onSwitch({ car_id: c.car_id })}
          className="flex items-center justify-between gap-3 rounded-2xl bg-white/80 px-4 py-3 text-start text-sm shadow-sm ring-1 ring-white transition hover:-translate-y-0.5 hover:bg-white hover:shadow-md disabled:opacity-50"
        >
          <span className="min-w-0 truncate">
            <span className="font-display font-semibold text-ink-900">{c.model_name}</span>
            <span className="text-ink-500"> · {c.drive}</span>
          </span>
          <span className="num chip shrink-0 bg-peach-100 text-peach-700">{`שווי ${ils(c.value_of_use)}`}</span>
        </button>
      ))}
      {canWaive && (
        <button
          type="button"
          disabled={locked}
          onClick={() => onSwitch({ waiver: true })}
          className="flex items-center justify-between gap-3 rounded-2xl border border-dashed border-blush-200 px-4 py-3 text-start text-sm font-semibold text-blush-700 transition hover:bg-white/60 disabled:opacity-50"
        >
          ויתור על רכב וקבלת אחזקה
          <span aria-hidden="true">‹</span>
        </button>
      )}
      <QuickSwitch {...props} />
    </div>
  );
}

/* ---------- profile ---------- */

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
  const missing = state.missing_labels.length;
  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h3 className="font-display text-lg font-semibold text-ink-900">הנתונים שלך</h3>
        <span className={`chip ${missing ? "bg-butter-100 text-butter-700" : "bg-mint-100 text-mint-700"}`}>
          {missing ? `חסרים ${missing} פרטים` : "✓ הנתונים הושלמו"}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        {facts.map(([k, val, field]) => (
          <div key={k} className="min-w-0 rounded-2xl bg-lilac-50/80 px-3 py-2">
            <dt className="text-[11px] text-ink-400">{k}</dt>
            <dd
              className={
                val
                  ? "num truncate text-start font-display font-semibold text-ink-900"
                  : emptyLabel(field) === "חסר"
                    ? "font-medium text-blush-700"
                    : "text-ink-400"
              }
            >
              {val ?? emptyLabel(field)}
            </dd>
          </div>
        ))}
      </dl>
      {missing > 0 && (
        <p className="mt-3 rounded-2xl bg-butter-50 px-3 py-2 text-xs text-ink-700 ring-1 ring-butter-200">
          <span className="font-semibold">עוד חסר לחישוב:</span> {state.missing_labels.join(" · ")}
        </p>
      )}
    </div>
  );
}

/* ---------- states without a result ---------- */

function Blocked({ state, busy, onSwitch }: Props & { state: StateView }) {
  const pc = state.precheck!;
  return (
    <div className="rounded-[2rem] bg-linear-to-br from-blush-100 via-peach-50 to-white p-6 ring-1 ring-white" role="alert" data-testid="blocked">
      <span className="chip bg-white/80 font-semibold text-blush-700">הבקשה אינה אפשרית</span>
      <h3 className="mt-3 font-display text-2xl font-semibold text-ink-900">{pc.requested}</h3>
      <p className="mt-1 text-ink-600">{pc.reason}</p>
      {pc.alternatives && pc.alternatives.length > 0 && (
        <>
          <p className="mt-5 text-sm font-semibold text-ink-900">חלופות מותרות - לחצו לבדיקה:</p>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {pc.alternatives.map((a) => (
              <button
                key={a.car_id}
                type="button"
                disabled={busy}
                onClick={() => onSwitch({ car_id: a.car_id })}
                className="rounded-2xl bg-white/85 px-4 py-3 text-start text-sm shadow-sm ring-1 ring-white transition hover:-translate-y-0.5 hover:bg-white hover:shadow-md disabled:opacity-50"
              >
                <span className="font-display font-semibold text-ink-900">{a.model_name}</span>
                <span className="mt-0.5 block text-xs text-ink-500">
                  קבוצה {a.group} · {a.drive} · שווי שימוש <span className="num">{num(a.value_of_use)} ₪</span>
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function ComparisonDashboard(props: Props) {
  const { state } = props;
  const c = state?.last_comparison ?? null;

  if (!state) {
    return (
      <section className="flex min-h-[24rem] items-center justify-center rounded-[2rem] border-2 border-dashed border-lilac-200 bg-white/50 p-8 text-center text-ink-500">
        <div>
          <span className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-3xl bg-linear-to-br from-peach-200 via-blush-100 to-lilac-200 text-2xl">
            🚗
          </span>
          <p className="font-display text-3xl font-semibold text-ink-900">כאן תופיע ההשוואה</p>
          <p className="mt-2 text-sm">נטו היום מול נטו בחלופה, ההפרש והפירוט המלא - מתעדכן אוטומטית.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-5" aria-label="השוואת נטו">
      {c && c.is_eligible && c.net_alt !== null ? (
        <>
          <Hero c={c} />
          <Breakdown c={c} />
        </>
      ) : c && !c.is_eligible ? (
        <div className="rounded-[2rem] bg-linear-to-br from-blush-100 via-peach-50 to-white p-6 ring-1 ring-white" role="alert">
          <span className="chip bg-white/80 font-semibold text-blush-700">החלופה המבוקשת אינה אפשרית</span>
          <p className="mt-3 text-ink-700">{c.reason}</p>
          <p className="mt-2 text-sm text-ink-500">
            נטו היום: <span className="num font-semibold text-ink-900">{ils(c.net_today)}</span> · אפשר לבחור חלופה
            מותרת למטה.
          </p>
        </div>
      ) : state.precheck?.eligible === false ? (
        <Blocked {...props} state={state} />
      ) : (
        <div className="rounded-[2rem] border-2 border-dashed border-lilac-200 bg-white/50 p-8 text-center text-ink-500">
          ההשוואה תופיע ברגע שיהיו כל הנתונים וחלופה מבוקשת.
        </div>
      )}

      <AllOptions
        ranking={props.ranking ?? null}
        wanted={!!props.rankingWanted}
        onRequest={props.onRequestRanking}
        currentDiff={c && c.is_eligible ? c.net_diff : null}
        busy={props.busy}
        locked={state.waiting_for_rep}
        onSwitch={props.onSwitch}
      />

      <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <ProfileSummary state={state} />
        <Alternatives {...props} />
      </div>
    </section>
  );
}
