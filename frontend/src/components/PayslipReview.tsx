import { useState } from "react";
import type { PayslipExtraction, PayslipFieldName, PayslipValues } from "../api";

interface Props {
  extraction: PayslipExtraction;
  busy: boolean;
  onConfirm: (values: PayslipValues) => void;
  onCancel: () => void;
  /** the employee agreed to send the page image of an unreadable PDF to the model */
  onReadImage?: () => void;
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

/** An unreadable payslip asks for consent first; a read one shows its values for review. */
export function PayslipReview(props: Props) {
  if (props.extraction.unreadable) return <ImageConsent {...props} />;
  // a re-read (text -> image) starts the review afresh
  return <ReviewValues key={props.extraction.source ?? ""} {...props} />;
}

/** The PDF has no readable text: only its image can be read - and an image cannot be
 *  redacted, so the employee decides. */
function ImageConsent({ extraction, busy, onCancel, onReadImage }: Props) {
  return (
    <div className="border-t border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950" data-testid="payslip-consent">
      <p className="font-semibold">⚠️ {extraction.unreadable}</p>
      {extraction.vision_available && onReadImage ? (
        <>
          <p className="mt-1">
            אפשר לשלוח <strong>תמונה של התלוש</strong> לקריאה במודל בינה מלאכותית (Gemini). התמונה כוללת את{" "}
            <strong>כל</strong> פרטי התלוש, כולל ת״ז וחשבון בנק. הקובץ לא נשמר אצלנו, ולפני שמירה תוכלו לבדוק
            ולאשר כל ערך.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={onReadImage}
              className="rounded-lg bg-amber-600 px-4 py-1.5 font-semibold text-white hover:bg-amber-700 disabled:opacity-40"
            >
              שליחה לקריאה
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onCancel}
              className="rounded-lg border border-slate-300 bg-white px-4 py-1.5 hover:bg-slate-100"
            >
              ביטול – אמסור את הנתונים בצ׳אט
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          onClick={onCancel}
          className="mt-3 rounded-lg border border-slate-300 bg-white px-4 py-1.5 hover:bg-slate-100"
        >
          סגירה – אמסור את הנתונים בצ׳אט
        </button>
      )}
    </div>
  );
}

/** Values read from the payslip, each beside the line it came from. The employee may
 *  correct or untick any of them; only what they confirm is saved. */
function ReviewValues({ extraction, busy, onConfirm, onCancel }: Props) {
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
      {extraction.source === "image" ? (
        <div className="mt-1 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950" data-testid="image-source">
          <p className="font-semibold">
            ⚠️ לא היה בקובץ טקסט קריא, ולכן התלוש נקרא מתמונה על ידי מודל בינה מלאכותית - בדקו היטב כל
            ערך מול התלוש.
          </p>
          {extraction.checks.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {extraction.checks.map((c, i) => (
                <li key={i} className={c.ok ? "text-emerald-800" : "font-semibold text-red-800"}>
                  {c.ok ? "✓" : "✗"} <span className="num">{c.text}</span>
                </li>
              ))}
            </ul>
          )}
          {extraction.checks.some((c) => !c.ok) && (
            <p className="mt-1 font-semibold text-red-800">
              בדיקת החשבון של התלוש נכשלה - ייתכן שספרה נקראה לא נכון.
            </p>
          )}
        </div>
      ) : (
        <p className="mt-0.5 text-xs text-teal-800">
          פרטים מזהים (ת״ז, חשבון בנק, כתובת, טלפון) הושחרו לפני העיבוד, והקובץ עצמו לא נשמר. רק מה
          שתאשרו יישמר.
        </p>
      )}

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
