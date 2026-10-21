import { useState } from "react";
import type { PayslipExtraction, PayslipFieldName, PayslipValues } from "../api";

interface Props {
  extraction: PayslipExtraction;
  busy: boolean;
  onConfirm: (values: PayslipValues) => void;
  onCancel: () => void;
}

const UNIT: Partial<Record<PayslipFieldName, string>> = {
  base_salary: "₪",
  commissions_bonuses: "₪",
  current_participation: "₪",
  pension_pct: "%",
  hishtalmut_pct: "%",
};

const shortLabel = (label: string) => label.split(" (")[0];

interface Row {
  include: boolean;
  text: string;
}

/** Values read from the payslip, each beside the line it came from. The employee may
 *  correct or untick any of them; only what they confirm is saved. */
export function PayslipReview({ extraction, busy, onConfirm, onCancel }: Props) {
  const [rows, setRows] = useState<Record<string, Row>>(() =>
    Object.fromEntries(extraction.fields.map((f) => [f.field, { include: true, text: String(f.value) }])),
  );

  const update = (field: string, patch: Partial<Row>) =>
    setRows((r) => ({ ...r, [field]: { ...r[field], ...patch } }));

  const values: PayslipValues = {};
  let invalid = false;
  for (const f of extraction.fields) {
    const row = rows[f.field];
    if (!row.include) continue;
    if (f.field === "occupation") {
      values.occupation = row.text;
      continue;
    }
    const n = Number(row.text.replace(/,/g, ""));
    if (row.text.trim() === "" || !Number.isFinite(n) || n < 0) invalid = true;
    else values[f.field] = n;
  }
  const count = Object.keys(values).length;

  return (
    <div className="border-t border-teal-200 bg-teal-50/60 px-4 py-3 text-sm" data-testid="payslip-review">
      <p className="font-semibold text-teal-900">📄 נתונים שזוהו בתלוש - בדקו ואשרו</p>
      <p className="mt-0.5 text-xs text-teal-800">
        פרטים מזהים (ת״ז, חשבון בנק, כתובת, טלפון) הושחרו לפני העיבוד, והקובץ עצמו לא נשמר. רק מה שתאשרו
        יישמר.
      </p>

      {extraction.fields.length > 0 ? (
        <table className="mt-2 w-full">
          <tbody>
            {extraction.fields.map((f) => {
              const row = rows[f.field];
              const label = shortLabel(f.label);
              return (
                <tr key={f.field} className="border-t border-teal-100 align-top">
                  <td className="py-1.5 pe-2">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={row.include}
                        onChange={(e) => update(f.field, { include: e.target.checked })}
                        aria-label={`לכלול: ${label}`}
                      />
                      <span>{label}</span>
                    </label>
                  </td>
                  <td className="py-1.5 pe-2">
                    {f.field === "occupation" ? (
                      <span className="font-semibold">{row.text}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        <input
                          inputMode="decimal"
                          value={row.text}
                          disabled={!row.include}
                          onChange={(e) => update(f.field, { text: e.target.value })}
                          aria-label={`ערך: ${label}`}
                          className="num w-24 rounded-lg border border-slate-300 bg-white px-2 py-0.5 disabled:opacity-40"
                        />
                        <span className="text-slate-500">{UNIT[f.field] ?? ""}</span>
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 text-xs text-slate-500" title="השורה בתלוש">
                    ״{f.evidence}״
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="mt-2 text-teal-900">לא זיהיתי בתלוש נתונים שאפשר לאמת. אפשר למסור אותם בצ׳אט.</p>
      )}

      {extraction.not_found.length > 0 && (
        <p className="mt-2 text-xs text-slate-600">
          לא נמצאו בתלוש (אשאל עליהם בצ׳אט): {extraction.not_found.map(shortLabel).join(" · ")}
        </p>
      )}
      {extraction.rejected.length > 0 && (
        <p className="mt-1 text-xs text-amber-800">
          לא אומתו מול התלוש ולכן לא נלקחו: {extraction.rejected.join(" · ")}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy || invalid || count === 0}
          onClick={() => onConfirm(values)}
          className="rounded-lg bg-teal-600 px-4 py-1.5 font-semibold text-white hover:bg-teal-700 disabled:opacity-40"
        >
          אישור ושמירה
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="rounded-lg border border-slate-300 bg-white px-4 py-1.5 hover:bg-slate-100"
        >
          ביטול
        </button>
        {invalid && <span className="self-center text-xs text-red-700">יש ערך לא תקין</span>}
      </div>
    </div>
  );
}
