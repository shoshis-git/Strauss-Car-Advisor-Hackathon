import { useEffect, useRef, useState } from "react";
import { api, ApiError, authOf, type Auth, type ConversationSummary } from "../api";
import { whenOf } from "../format";

const buttonClass =
  "shrink-0 rounded-full bg-white px-3.5 py-1.5 text-xs font-semibold text-ink-700 ring-1 ring-ink-100 " +
  "transition hover:bg-lilac-50 hover:ring-lilac-200 disabled:opacity-50";

/** Logged-in employees: start a new conversation, or go back to an earlier one. */
export function ConversationMenu({
  currentId,
  disabled,
  onSwitch,
}: {
  currentId: string;
  disabled: boolean;
  onSwitch: (auth: Auth) => void;
}) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<ConversationSummary[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  // the list is fetched every time it opens (titles change as the conversation goes on)
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setList(null);
    setError(null);
    api
      .conversations()
      .then((l) => !cancelled && setList(l))
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [open]);

  // close on a click outside / Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const go = async (action: () => ReturnType<typeof api.newConversation>) => {
    setBusy(true);
    setError(null);
    try {
      onSwitch(authOf(await action()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };

  const locked = disabled || busy;

  return (
    <div ref={root} className="relative flex items-center gap-2">
      <button type="button" className={buttonClass} disabled={locked} onClick={() => void go(api.newConversation)}>
        + שיחה חדשה
      </button>
      <button
        type="button"
        className={buttonClass}
        disabled={locked}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="true"
      >
        השיחות שלי ▾
      </button>
      {error && !open && (
        <span className="text-xs text-blush-700" role="alert">
          {error}
        </span>
      )}

      {open && (
        <div
          className="absolute end-0 top-full z-20 mt-2 w-72 overflow-hidden rounded-2xl bg-white p-1.5 shadow-[0_16px_40px_-12px_rgb(110_86_180/0.4)] ring-1 ring-ink-100"
          aria-label="השיחות שלי"
        >
          {error && (
            <p className="px-3 py-2 text-xs text-blush-700" role="alert">
              {error}
            </p>
          )}
          {!list && !error && <p className="px-3 py-2 text-xs text-ink-400">טוענים…</p>}
          {list && (
            <ul className="max-h-80 overflow-y-auto">
              {list.map((c) => {
                const current = c.session_id === currentId;
                return (
                  <li key={c.session_id}>
                    <button
                      type="button"
                      disabled={current || locked}
                      onClick={() => void go(() => api.openConversation(c.session_id))}
                      aria-current={current ? "true" : undefined}
                      className={
                        "w-full rounded-xl px-3 py-2 text-start transition " +
                        (current ? "bg-lilac-50 ring-1 ring-lilac-200" : "hover:bg-ink-50 disabled:opacity-60")
                      }
                    >
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-sm font-medium text-ink-900">{c.title}</span>
                        <span className="num shrink-0 text-[11px] text-ink-400">{whenOf(c.updated_at)}</span>
                      </span>
                      {(current || c.waiting_for_rep) && (
                        <span className="mt-0.5 flex gap-2 text-[11px]">
                          {current && <span className="text-lilac-700">השיחה הנוכחית</span>}
                          {c.waiting_for_rep && <span className="text-peach-700">⏳ ממתין לנציג</span>}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
