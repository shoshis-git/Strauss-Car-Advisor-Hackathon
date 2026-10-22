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

const primaryBtn =
  "rounded-full bg-lilac-600 px-5 py-2 text-sm font-semibold text-white shadow-sm shadow-lilac-400/40 transition hover:bg-lilac-700 disabled:opacity-40";
const secondaryBtn =
  "rounded-full bg-white px-5 py-2 text-sm font-medium text-ink-700 ring-1 ring-ink-200 transition hover:bg-ink-50 disabled:opacity-40";

const FileIcon = () => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" strokeLinejoin="round" />
    <path d="M14 3v5h5M9 13h6M9 17h4" strokeLinecap="round" />
  </svg>
);

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
    <div
      className="w-full max-w-[95%] rounded-3xl bg-butter-50 p-4 text-sm text-ink-900 shadow-sm ring-1 ring-butter-200"
      data-testid="payslip-consent"
    >
      <p className="font-semibold">⚠️ {extraction.unreadable}</p>
      {extraction.vision_available && onReadImage ? (
        <>
          <p className="mt-1 leading-relaxed">
            אפשר לשלוח <strong>תמונה של התלוש</strong> לקריאה במודל בינה מלאכותית (Gemini). התמונה כוללת את{" "}
            <strong>כל</strong> פרטי התלוש, כולל ת״ז וחשבון בנק. הקובץ לא נשמר אצלנו, ולפני שמירה תוכלו לבדוק
            ולאשר כל ערך.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={onReadImage}
              className="rounded-full bg-peach-300 px-5 py-2 text-sm font-semibold text-ink-900 transition hover:bg-peach-200 disabled:opacity-40"
            >
              שליחה לקריאה
            </button>
            <button type="button" disabled={busy} onClick={onCancel} className={secondaryBtn}>
              ביטול – אמסור את הנתונים בצ׳אט
            </button>
          </div>
        </>
      ) : (
        <button type="button" onClick={onCancel} className={`mt-3 ${secondaryBtn}`}>
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
    <div className="w-full max-w-[95%] rounded-3xl bg-white p-4 text-sm shadow-sm ring-1 ring-ink-100" data-testid="payslip-review">
      <div className="flex items-start gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-lilac-100 text-lilac-700">
          <FileIcon />
        </span>
        <div className="min-w-0">
          <p className="font-display text-base font-semibold text-ink-900">נתונים שזוהו בתלוש - בדקו ואשרו</p>
          <p className="text-xs text-ink-500">
            <span className="num">{extraction.fields.length}</span> שדות זוהו
          </p>
        </div>
      </div>

      {extraction.source === "image" ? (
        <div className="mt-3 rounded-2xl bg-butter-50 px-3 py-2 text-xs text-ink-900 ring-1 ring-butter-200" data-testid="image-source">
          <p className="font-semibold">
            ⚠️ לא היה בקובץ טקסט קריא, ולכן התלוש נקרא מתמונה על ידי מודל בינה מלאכותית - בדקו היטב כל
            ערך מול התלוש.
          </p>
          {extraction.checks.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {extraction.checks.map((c, i) => (
                <li key={i} className={c.ok ? "text-mint-700" : "font-semibold text-blush-700"}>
                  {c.ok ? "✓" : "✗"} <span className="num">{c.text}</span>
                </li>
              ))}
            </ul>
          )}
          {extraction.checks.some((c) => !c.ok) && (
            <p className="mt-1 font-semibold text-blush-700">
              בדיקת החשבון של התלוש נכשלה - ייתכן שספרה נקראה לא נכון.
            </p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-xs leading-relaxed text-ink-500">
          פרטים מזהים (ת״ז, חשבון בנק, כתובת, טלפון) הושחרו לפני העיבוד, והקובץ עצמו לא נשמר. רק מה
          שתאשרו יישמר.
        </p>
      )}

      {extraction.fields.length > 0 ? (
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {extraction.fields.map((f) => {
            const row = rows[f.field];
            const label = shortLabel(f.label);
            return (
              <div
                key={f.field}
                className={`rounded-2xl p-3 ring-1 transition ${
                  row.include ? "bg-mint-50 ring-mint-200" : "bg-ink-50 opacity-70 ring-ink-100"
                } ${f.field === "occupation" ? "sm:col-span-2" : ""}`}
              >
                <label className="flex items-center gap-2 text-xs font-medium text-ink-600">
                  <input
                    type="checkbox"
                    checked={row.include}
                    onChange={(e) => update(f.field, { include: e.target.checked })}
                    aria-label={`לכלול: ${label}`}
                    className="h-4 w-4 accent-lilac-600"
                  />
                  <span>{label}</span>
                </label>
                <div className="mt-1.5">
                  {f.field === "occupation" ? (
                    <span className="font-semibold text-ink-900">{row.text}</span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <input
                        inputMode="decimal"
                        value={row.text}
                        disabled={!row.include}
                        onChange={(e) => update(f.field, { text: e.target.value })}
                        aria-label={`ערך: ${label}`}
                        className="num w-full min-w-0 rounded-xl border border-white bg-white px-2.5 py-1 font-display font-semibold text-ink-900 focus:border-lilac-300 focus:outline-none focus:ring-2 focus:ring-lilac-100 disabled:opacity-40"
                      />
                      <span className="text-xs text-ink-500">{UNIT[f.field] ?? ""}</span>
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-[11px] text-ink-400" title="השורה בתלוש">
                  ״{f.evidence}״
                </p>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="mt-2 text-ink-700">לא זיהיתי בתלוש נתונים שאפשר לאמת. אפשר למסור אותם בצ׳אט.</p>
      )}

      {extraction.not_found.length > 0 && (
        <p className="mt-2 text-xs text-ink-500">
          לא נמצאו בתלוש (אשאל עליהם בצ׳אט): {extraction.not_found.map(shortLabel).join(" · ")}
        </p>
      )}
      {extraction.rejected.length > 0 && (
        <p className="mt-1 text-xs text-peach-700">
          לא אומתו מול התלוש ולכן לא נלקחו: {extraction.rejected.join(" · ")}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy || invalid || count === 0} onClick={() => onConfirm(values)} className={primaryBtn}>
          אישור ושמירה
        </button>
        <button type="button" disabled={busy} onClick={onCancel} className={secondaryBtn}>
          ביטול
        </button>
        {invalid && <span className="text-xs text-blush-700">יש ערך לא תקין</span>}
      </div>
    </div>
  );
}
