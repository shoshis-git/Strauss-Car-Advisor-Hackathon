import { useEffect, useRef, useState, type FormEvent } from "react";
import type { StateView } from "../api";
import { boldSegments, timeOf } from "../format";
import type { ChatItem } from "../useEmployeeSession";

interface Props {
  items: ChatItem[];
  state: StateView | null;
  busy: boolean;
  error: string | null;
  notice?: string | null;
  onSend: (text: string) => void;
  onUpload: (fileName: string) => void;
}

const ROLE_STYLE: Record<string, { label: string; bubble: string; align: string }> = {
  employee: { label: "את/ה", bubble: "bg-teal-600 text-white", align: "justify-start" },
  assistant: { label: "יועץ הרכב", bubble: "bg-white border border-slate-200", align: "justify-end" },
  representative: {
    label: "נציג/ת שכר",
    bubble: "bg-amber-50 border border-amber-300 text-amber-950",
    align: "justify-end",
  },
};

function RichText({ text }: { text: string }) {
  return (
    <>
      {boldSegments(text).map((s, i) => (s.bold ? <strong key={i}>{s.text}</strong> : <span key={i}>{s.text}</span>))}
    </>
  );
}

export function ChatPanel({ items, state, busy, error, notice, onSend, onUpload }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items.length, busy]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  }

  const visible = items; // internal tool messages never leave the server

  return (
    <section className="flex min-h-[28rem] flex-1 flex-col rounded-2xl border border-slate-200 bg-slate-100/60 shadow-sm">
      <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
        {visible.length === 0 && !busy && (
          <div className="mx-auto mt-10 max-w-sm text-center text-slate-500">
            <p className="text-lg font-semibold text-slate-700">שלום! אני יועץ הרכב 👋</p>
            <p className="mt-2 text-sm">
              ספר/י לי מה התפקיד שלך ומה את/ה שוקל/ת (רכב מסוים, חשמלי, ויתור) - ואבדוק מיד אם זה
              אפשרי, ואז אחשב כמה זה ישנה בנטו. או בחר/י עובד הדגמה למעלה.
            </p>
            <p className="mt-2 text-xs text-slate-400">
              📎 אפשר גם לצרף תלוש או חוזה (כרגע הדמיה - הנתונים עדיין נמסרים בצ׳אט).
            </p>
          </div>
        )}
        {visible.map((m, i) => {
          const style = ROLE_STYLE[m.role] ?? ROLE_STYLE.assistant;
          const local = "local" in m;
          return (
            <div key={i} className={`flex ${style.align}`}>
              <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 shadow-sm ${style.bubble}`}>
                <div className="mb-0.5 flex items-center gap-2 text-[11px] opacity-75">
                  <span className="font-semibold">{style.label}</span>
                  <span className="num">{timeOf(m.created_at)}</span>
                  {local && <span>(הדמיה - לא נשלח לשרת)</span>}
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
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
              <span className="inline-flex gap-1 align-middle">
                <span className="h-2 w-2 animate-bounce rounded-full bg-teal-500 [animation-delay:-0.3s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-teal-500 [animation-delay:-0.15s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-teal-500" />
              </span>{" "}
              היועץ מעבד…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {state?.waiting_for_rep && (
        <div className="border-t border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950" role="alert">
          <p className="font-semibold">⏳ ממתינים לתשובת נציג/ת שכר ותחבורה</p>
          <p className="mt-0.5">{state.rep_question}</p>
          <p className="mt-1 text-xs text-amber-800">החישוב יתעדכן כאן אוטומטית ברגע שתתקבל תשובה.</p>
        </div>
      )}
      {notice && !error && (
        <div className="border-t border-sky-200 bg-sky-50 px-4 py-2 text-xs text-sky-900" data-testid="llm-notice">
          ℹ️ {notice}
        </div>
      )}
      {error && (
        <div className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800" role="alert">
          {error}
        </div>
      )}

      <form onSubmit={submit} className="flex items-end gap-2 border-t border-slate-200 bg-white p-3">
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f.name);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          title="העלאת תלוש / חוזה (הדמיה)"
          aria-label="העלאת תלוש או חוזה (הדמיה)"
          className="rounded-xl border border-slate-300 px-3 py-2.5 text-lg hover:bg-slate-100"
        >
          📎
        </button>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          rows={1}
          placeholder="למשל: שכר בסיס 14,200, קבוצה ג׳, רוצה לעבור לאטו 3"
          aria-label="הודעה ליועץ"
          className="max-h-32 min-h-[2.75rem] flex-1 resize-y rounded-xl border border-slate-300 px-3 py-2.5 focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-200"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="rounded-xl bg-teal-600 px-5 py-2.5 font-semibold text-white hover:bg-teal-700 disabled:opacity-40"
        >
          שליחה
        </button>
      </form>
    </section>
  );
}
