import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Message, StateView } from "../api";
import { boldSegments, timeOf } from "../format";

interface Props {
  items: Message[];
  state: StateView | null;
  busy: boolean;
  error: string | null;
  notice?: string | null;
  onSend: (text: string) => void;
  onUpload: (file: File) => void;
  /** top bar of the panel (who the conversation is with, demo picker) */
  header?: ReactNode;
  /** e.g. the payslip review card, shown at the end of the conversation */
  review?: ReactNode;
}

const ROLE_STYLE: Record<string, { label: string; bubble: string; align: string; meta: string }> = {
  employee: {
    label: "את/ה",
    bubble: "bg-linear-to-br from-lilac-200 to-baby-200 text-ink-900 rounded-se-md",
    align: "justify-start",
    meta: "text-ink-500",
  },
  assistant: {
    label: "יועץ הרכב",
    bubble: "bg-white text-ink-700 ring-1 ring-ink-100 rounded-ss-md",
    align: "justify-end",
    meta: "text-ink-400",
  },
  representative: {
    label: "נציג/ת שכר",
    bubble: "bg-butter-100 text-ink-900 ring-1 ring-butter-200 rounded-ss-md",
    align: "justify-end",
    meta: "text-butter-700",
  },
};

function RichText({ text }: { text: string }) {
  return (
    <>
      {boldSegments(text).map((s, i) =>
        s.bold ? (
          <strong key={i} className="font-semibold text-ink-900">
            {s.text}
          </strong>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

const PaperclipIcon = () => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path
      d="M21 11.5l-8.6 8.6a5 5 0 01-7.1-7.1l8.6-8.6a3.5 3.5 0 015 5l-8.6 8.6a2 2 0 01-2.8-2.8l7.9-7.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const SendIcon = () => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
    <path d="M19 12H5M11 6l-6 6 6 6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export function ChatPanel({ items, state, busy, error, notice, onSend, onUpload, header, review }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items.length, busy, review]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  }

  return (
    <section className="card flex min-h-[32rem] flex-1 flex-col overflow-hidden">
      {header && <div className="border-b border-ink-100 px-5 py-4">{header}</div>}

      <div className="scroll-thin flex-1 space-y-3 overflow-y-auto bg-lilac-50/40 p-4" aria-live="polite">
        {items.length === 0 && !busy && !review && (
          <div className="mx-auto mt-8 max-w-sm text-center text-ink-500">
            <span className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-3xl bg-linear-to-br from-mint-200 via-baby-200 to-lilac-200 text-2xl">
              👋
            </span>
            <p className="font-display text-2xl font-semibold text-ink-900">שלום, אני יועץ הרכב</p>
            <p className="mt-2 text-sm leading-relaxed">
              ספר/י לי מה התפקיד שלך ומה את/ה שוקל/ת (רכב מסוים, חשמלי, ויתור) - ואבדוק מיד אם זה
              אפשרי, ואז אחשב כמה זה ישנה בנטו. או בחר/י עובד הדגמה למעלה.
            </p>
            <p className="mt-4 rounded-2xl bg-white/80 px-4 py-2.5 text-xs leading-relaxed text-ink-500 ring-1 ring-ink-100">
              📎 אפשר גם לצרף תלוש שכר דיגיטלי (PDF): הנתונים יוצגו לאישורך לפני שמירה, פרטים מזהים
              מושחרים והקובץ לא נשמר.
            </p>
          </div>
        )}
        {items.map((m, i) => {
          const style = ROLE_STYLE[m.role] ?? ROLE_STYLE.assistant;
          return (
            <div key={i} className={`flex ${style.align}`}>
              <div className={`max-w-[88%] rounded-3xl px-4 py-2.5 text-sm shadow-sm ${style.bubble}`}>
                <div className={`mb-0.5 flex items-center gap-2 text-[10px] ${style.meta}`}>
                  <span className="font-semibold">{style.label}</span>
                  <span className="num">{timeOf(m.created_at)}</span>
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">
                  <RichText text={m.content} />
                </div>
              </div>
            </div>
          );
        })}
        {busy && (
          <div className="flex justify-end" role="status">
            <div className="rounded-3xl rounded-ss-md bg-white px-4 py-3 text-sm text-ink-500 shadow-sm ring-1 ring-ink-100">
              <span className="inline-flex gap-1 align-middle">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-lilac-500 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-blush-500 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-mint-500" />
              </span>{" "}
              היועץ מעבד…
            </div>
          </div>
        )}
        {review && <div className="flex justify-end">{review}</div>}
        <div ref={endRef} />
      </div>

      {state?.waiting_for_rep && (
        <div className="border-t border-butter-200 bg-butter-50 px-5 py-3 text-sm text-ink-900" role="alert">
          <p className="font-semibold">⏳ ממתינים לתשובת נציג/ת שכר ותחבורה</p>
          <p className="mt-0.5 text-ink-700">{state.rep_question}</p>
          <p className="mt-1 text-xs text-butter-700">החישוב יתעדכן כאן אוטומטית ברגע שתתקבל תשובה.</p>
        </div>
      )}
      {notice && !error && (
        <div className="border-t border-baby-200 bg-baby-50 px-5 py-2 text-xs text-baby-700" data-testid="llm-notice">
          ℹ️ {notice}
        </div>
      )}
      {error && (
        <div className="border-t border-blush-200 bg-blush-50 px-5 py-2 text-sm text-blush-700" role="alert">
          {error}
        </div>
      )}

      <form onSubmit={submit} className="flex items-end gap-2 border-t border-ink-100 bg-white/70 p-3">
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          aria-label="קובץ תלוש שכר"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy || !!state?.waiting_for_rep}
          title="העלאת תלוש שכר (PDF)"
          aria-label="העלאת תלוש שכר (PDF)"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-peach-100 text-peach-700 transition hover:bg-peach-200 disabled:opacity-40"
        >
          <PaperclipIcon />
        </button>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          rows={1}
          placeholder="שאל/י על רכב אחר, ויתור, מסלול משפחה…"
          aria-label="הודעה ליועץ"
          className="max-h-32 min-h-[2.75rem] flex-1 resize-none rounded-2xl border border-ink-100 bg-ink-50 px-4 py-2.5 text-sm text-ink-900 transition placeholder:text-ink-400 focus:border-lilac-300 focus:bg-white focus:outline-none focus:ring-4 focus:ring-lilac-100"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          aria-label="שליחה"
          title="שליחה"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-lilac-600 text-white shadow-md shadow-lilac-400/40 transition hover:bg-lilac-700 disabled:opacity-40 disabled:shadow-none"
        >
          <SendIcon />
        </button>
      </form>
    </section>
  );
}
