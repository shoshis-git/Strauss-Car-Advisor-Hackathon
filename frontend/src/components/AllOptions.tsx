import { useState } from "react";
import type { RankedOption, Ranking } from "../api";
import { ils, signedIls, trend } from "../format";

interface Props {
  ranking: Ranking | null;
  busy: boolean;
  locked: boolean;
  onSwitch: (choice: { car_id: string } | { waiver: true }) => void;
}

const COLLAPSED = 5;

const DIFF_TEXT = { up: "text-mint-700", down: "text-blush-700", flat: "text-ink-500" } as const;
const DIFF_BAR = { up: "bg-mint-300", down: "bg-blush-200", flat: "bg-ink-100" } as const;

function details(o: RankedOption): string {
  if (o.kind === "waiver") return `אחזקת רכב ${ils(o.maintenance_allowance)} · חייבת במס`;
  const parts = [
    o.family_track ? "מסלול משפחתי" : `קבוצה ${o.group}`,
    o.drive,
    `שווי שימוש ${ils(o.value_of_use)}`,
    `השתתפות ${ils(o.participation)}`,
  ];
  if (o.family_gap_deduction) parts.push(`חלקך בפער ${ils(o.family_gap_deduction)}`);
  return parts.filter(Boolean).join(" · ");
}

function Highlight({ title, o }: { title: string; o: RankedOption }) {
  const dir = trend(o.net_diff);
  return (
    <div className="min-w-0 rounded-2xl bg-white/80 px-4 py-3 ring-1 ring-white">
      <div className="text-[11px] font-medium text-ink-500">{title}</div>
      <div className="mt-0.5 flex items-baseline justify-between gap-2">
        <span className="truncate font-display font-semibold text-ink-900">{o.model_name}</span>
        <span className={`num shrink-0 font-display font-semibold ${DIFF_TEXT[dir]}`}>{signedIls(o.net_diff)}</span>
      </div>
    </div>
  );
}

function OptionRow({ o, max, disabled, onPick }: { o: RankedOption; max: number; disabled: boolean; onPick: () => void }) {
  const dir = trend(o.net_diff);
  const width = `${Math.max(4, (Math.abs(o.net_diff) / max) * 100)}%`;
  return (
    <li>
      <button
        type="button"
        disabled={disabled || o.is_selected}
        onClick={onPick}
        aria-label={`${o.model_name}: ${signedIls(o.net_diff)} בחודש לעומת היום`}
        className={`group flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-start transition ${
          o.is_selected
            ? "bg-lilac-100 ring-1 ring-lilac-200"
            : "bg-white/70 ring-1 ring-white hover:-translate-y-0.5 hover:bg-white hover:shadow-md disabled:opacity-50 disabled:hover:translate-y-0"
        }`}
      >
        <span
          className={`num flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
            o.rank === 1 ? "bg-mint-200 text-mint-700" : "bg-ink-50 text-ink-500"
          }`}
        >
          {o.rank}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="font-display font-semibold text-ink-900">{o.model_name}</span>
            {o.is_electric && <span className="chip bg-baby-100 text-baby-700">⚡ חשמלי</span>}
            {o.family_track && <span className="chip bg-peach-100 text-peach-700">מסלול משפחתי</span>}
            {o.kind === "waiver" && <span className="chip bg-butter-100 text-butter-700">ויתור</span>}
            {o.is_selected && <span className="chip bg-white text-lilac-700">החלופה שמוצגת למעלה</span>}
          </span>
          <span className="mt-0.5 block truncate text-xs text-ink-500">{details(o)}</span>
          <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-ink-50">
            <span className={`block h-full rounded-full ${DIFF_BAR[dir]}`} style={{ width }} />
          </span>
        </span>
        <span className="shrink-0 text-end">
          <span className={`num block font-display text-lg font-semibold ${DIFF_TEXT[dir]}`}>{signedIls(o.net_diff)}</span>
          <span className="num block text-[11px] text-ink-400">נטו {ils(o.net_alt)}</span>
        </span>
      </button>
    </li>
  );
}

/** Every permitted alternative, ranked by the monthly net change (all numbers from the engine). */
export function AllOptions({ ranking, busy, locked, onSwitch }: Props) {
  const [expanded, setExpanded] = useState(false);
  if (!ranking) return null;

  if (!ranking.ok) {
    if (!ranking.missing_labels?.length) return null;
    return (
      <div className="rounded-[1.75rem] border-2 border-dashed border-mint-200 bg-white/50 p-5 text-sm text-ink-600" data-testid="all-options-pending">
        <span className="font-display font-semibold text-ink-900">כל האפשרויות שלך</span> - דירוג של כל הרכבים
        המותרים והוויתור לפי הנטו יופיע כשיהיו: {ranking.missing_labels.join(" · ")}
      </div>
    );
  }

  const options = ranking.options ?? [];
  if (!options.length) return null;
  const max = Math.max(...options.map((o) => Math.abs(o.net_diff)), 1);
  const shown = expanded ? options : options.slice(0, COLLAPSED);
  const best = ranking.best;
  const bestElectric = ranking.best_electric;
  const pick = (o: RankedOption) => onSwitch(o.kind === "waiver" ? { waiver: true } : { car_id: o.car_id! });

  return (
    <section
      className="rounded-[2rem] bg-linear-to-br from-mint-100 via-baby-50 to-lilac-50 p-5 shadow-[0_12px_32px_-14px_rgb(60_150_120/0.3)] ring-1 ring-white"
      aria-label="כל האפשרויות שלך"
      data-testid="all-options"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-display text-xl font-semibold text-ink-900">כל האפשרויות שלך</h3>
          <p className="mt-0.5 text-xs text-ink-500">
            נטו היום <span className="num font-semibold text-ink-700">{ils(ranking.net_today)}</span> ({ranking.current_label}) ·{" "}
            <span className="num">{ranking.count}</span> חלופות מותרות, מדורגות לפי השינוי בנטו החודשי
          </p>
        </div>
        <span className="chip bg-white/80 text-ink-600">
          קבוצה {ranking.group} · תקציב <span className="num">{ils(ranking.budget)}</span>
        </span>
      </div>

      {best && (
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Highlight title="הכי משתלם לך" o={best} />
          {bestElectric && bestElectric !== best && bestElectric.car_id !== best.car_id && (
            <Highlight title="החשמלי המשתלם ביותר" o={bestElectric} />
          )}
        </div>
      )}

      <ol className="mt-4 space-y-2">
        {shown.map((o) => (
          <OptionRow key={o.car_id ?? "waiver"} o={o} max={max} disabled={busy || locked} onPick={() => pick(o)} />
        ))}
      </ol>

      {options.length > COLLAPSED && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-3 w-full rounded-full bg-white/70 py-2 text-sm font-medium text-ink-700 ring-1 ring-white transition hover:bg-white"
        >
          {expanded ? "הצג פחות" : `הצג את כל ${options.length} האפשרויות`}
        </button>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-ink-500">
        לחיצה על חלופה מציגה את הפירוט המלא שלה למעלה. ההשתתפות בכל רכב היא לפי טבלת הרכבים של החברה; החישוב
        במנוע הקבוע, לפי אותם כללים כמו ההשוואה.
        {ranking.notes?.map((n) => (
          <span key={n} className="block">
            {n}.
          </span>
        ))}
      </p>
    </section>
  );
}
