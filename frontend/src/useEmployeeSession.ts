import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type Message,
  type PayslipExtraction,
  type PayslipValues,
  type StateView,
} from "./api";

const STORAGE_KEY = "car-advisor.session";
const POLL_MS = 3000;

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function writeStored(id: string | null) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable - session simply won't survive a refresh */
  }
}

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** Employee conversation: server is the source of truth, refreshed after every action. */
export function useEmployeeSession() {
  const [sessionId, setSessionId] = useState<string | null>(readStored);
  const [messages, setMessages] = useState<Message[]>([]);
  const [state, setState] = useState<StateView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // values read from an uploaded payslip, waiting for the employee's confirmation
  const [payslip, setPayslip] = useState<PayslipExtraction | null>(null);
  const activeId = useRef(sessionId);

  const adopt = useCallback((id: string | null) => {
    activeId.current = id;
    setSessionId(id);
    writeStored(id);
  }, []);

  const refresh = useCallback(async (id: string) => {
    const s = await api.session(id);
    if (activeId.current !== id) return; // user switched session meanwhile
    // one render: chat and dashboard never show different moments of the conversation
    setMessages(s.messages);
    setState(s.state);
  }, []);

  const run = useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // Recover the stored conversation after a browser refresh.
  useEffect(() => {
    const id = activeId.current;
    if (!id) return;
    refresh(id).catch((e) => {
      // 404 = gone; 422 = an old-format id from before server-generated ids
      if (e instanceof ApiError && (e.status === 404 || e.status === 422)) adopt(null);
      else setError(e instanceof ApiError ? e.message : String(e));
    });
  }, [refresh, adopt]);

  // While waiting for a representative, poll until the answer arrives.
  useEffect(() => {
    if (!sessionId || !state?.waiting_for_rep) return;
    const t = setInterval(() => {
      refresh(sessionId).catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(t);
  }, [sessionId, state?.waiting_for_rep, refresh]);

  const send = useCallback(
    (text: string) =>
      run(async () => {
        const optimistic: Message = {
          role: "employee",
          content: text,
          created_at: new Date().toISOString(),
        };
        setMessages((m) => [...m, optimistic]);
        const r = await api.chat(text, activeId.current);
        adopt(r.session_id);
        setNotice(r.notice ?? null);
        // state + messages are applied together by refresh (dashboard and chat stay in step)
        await refresh(r.session_id);
      }),
    [run, adopt, refresh],
  );

  const loadDemo = useCallback(
    (empId: string) =>
      run(async () => {
        adopt(null);
        setPayslip(null);
        setMessages([]);
        setState(null);
        const r = await api.loadDemo(empId); // the server opens a new conversation
        adopt(r.session_id);
        await refresh(r.session_id);
      }),
    [run, adopt, refresh],
  );

  const setAlternative = useCallback(
    (choice: { car_id: string } | { waiver: true }) =>
      run(async () => {
        const id = activeId.current;
        if (!id) return;
        await api.setAlternative(id, choice);
        await refresh(id);
      }),
    [run, refresh],
  );

  /** Read a payslip: the server proposes values; nothing is saved yet. */
  const uploadPayslip = useCallback(
    (file: File) =>
      run(async () => {
        setPayslip(null);
        if (!isPdf(file)) throw new ApiError(415, "אפשר להעלות רק תלוש שכר בפורמט PDF");
        setPayslip(await api.extractPayslip(file));
      }),
    [run],
  );

  /** The employee confirmed (possibly corrected) values: save them and continue the chat. */
  const confirmPayslip = useCallback(
    (values: PayslipValues) =>
      run(async () => {
        const r = await api.applyPayslip(activeId.current, values);
        setPayslip(null);
        adopt(r.session_id);
        setNotice(r.notice ?? null);
        await refresh(r.session_id);
      }),
    [run, adopt, refresh],
  );

  const cancelPayslip = useCallback(() => setPayslip(null), []);

  const reset = useCallback(() => {
    adopt(null);
    setMessages([]);
    setState(null);
    setError(null);
    setNotice(null);
    setPayslip(null);
  }, [adopt]);

  return {
    sessionId,
    messages,
    state,
    busy,
    error,
    notice,
    payslip,
    send,
    loadDemo,
    setAlternative,
    reset,
    uploadPayslip,
    confirmPayslip,
    cancelPayslip,
  };
}
