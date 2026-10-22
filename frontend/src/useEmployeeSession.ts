import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type Message,
  type PayslipExtraction,
  type PayslipValues,
  type StateView,
} from "./api";

const POLL_MS = 3000;

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** The logged-in employee's conversation: the server is the source of truth, refreshed
 *  after every action. The conversation was opened at login (sessionId comes with the
 *  token); after a browser refresh it is simply reloaded. */
export function useEmployeeSession(sessionId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [state, setState] = useState<StateView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // values read from an uploaded payslip, waiting for the employee's confirmation
  const [payslip, setPayslip] = useState<PayslipExtraction | null>(null);
  const payslipFile = useRef<File | null>(null);
  const activeId = useRef(sessionId);
  activeId.current = sessionId;

  const refresh = useCallback(async (id: string) => {
    const s = await api.session(id);
    if (activeId.current !== id) return; // logged out / switched meanwhile
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

  // Load the conversation (right after login, and after a browser refresh).
  useEffect(() => {
    refresh(sessionId).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [sessionId, refresh]);

  // While waiting for a representative, poll until the answer arrives.
  useEffect(() => {
    if (!state?.waiting_for_rep) return;
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
        setNotice(r.notice ?? null);
        // state + messages are applied together by refresh (dashboard and chat stay in step)
        await refresh(activeId.current);
      }),
    [run, refresh],
  );

  const setAlternative = useCallback(
    (choice: { car_id: string } | { waiver: true }) =>
      run(async () => {
        await api.setAlternative(activeId.current, choice);
        await refresh(activeId.current);
      }),
    [run, refresh],
  );

  /** Read a payslip: the server proposes values; nothing is saved yet. */
  const uploadPayslip = useCallback(
    (file: File) =>
      run(async () => {
        setPayslip(null);
        payslipFile.current = null;
        if (!isPdf(file)) throw new ApiError(415, "אפשר להעלות רק תלוש שכר בפורמט PDF");
        payslipFile.current = file; // kept only in this tab, for a consented re-read
        setPayslip(await api.extractPayslip(file));
      }),
    [run],
  );

  /** The text was unreadable and the employee agreed to send the page image to the model. */
  const readPayslipImage = useCallback(
    () =>
      run(async () => {
        const file = payslipFile.current;
        if (!file) return;
        setPayslip(await api.extractPayslip(file, true));
      }),
    [run],
  );

  /** The employee confirmed (possibly corrected) values: save them and continue the chat. */
  const confirmPayslip = useCallback(
    (values: PayslipValues) =>
      run(async () => {
        const r = await api.applyPayslip(activeId.current, values);
        setPayslip(null);
        payslipFile.current = null;
        setNotice(r.notice ?? null);
        await refresh(activeId.current);
      }),
    [run, refresh],
  );

  const cancelPayslip = useCallback(() => {
    setPayslip(null);
    payslipFile.current = null;
  }, []);

  return {
    sessionId,
    messages,
    state,
    busy,
    error,
    notice,
    payslip,
    send,
    setAlternative,
    uploadPayslip,
    readPayslipImage,
    confirmPayslip,
    cancelPayslip,
  };
}
