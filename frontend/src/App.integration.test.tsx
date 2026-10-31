/**
 * End-to-end: the real <App/> against the real backend (VITE_API_BASE, default :8000).
 * Skipped automatically when the backend is not running.
 * Chat-driven steps need the deterministic brain (backend started with LLM_PROVIDER=rules
 * and REP_API_KEY=e2e-rep-key-0123456789).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { API_BASE } from "./api";
import App from "./App";
// synthetic payslip with the pay data of sample employee E-004 (fictional person)
import e004Pdf from "../../backend/tests/fixtures/payslip_e004.pdf?inline";

async function backendInfo(): Promise<{ deterministic: boolean } | null> {
  try {
    const r = await fetch(`${API_BASE}/api/health`);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
const health = await backendInfo();
const deterministic = health?.deterministic === true;
// the backend under test is started with REP_API_KEY=e2e-rep-key-0123456789
const REP_KEY = "e2e-rep-key-0123456789";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(cleanup);

const T = { timeout: 15000 };

/** The login screen is the gate: continue as a guest and wait for the chat box. */
async function loginAsGuest(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "המשך כאורח" }, T));
  const box = await screen.findByLabelText("הודעה ליועץ", {}, T);
  await screen.findByText(/אני יועץ הרכב/, {}, T); // the guest greeting
  return box;
}

const authSid = () => (JSON.parse(sessionStorage.getItem("car-advisor.auth") ?? "{}") as { session_id?: string }).session_id;

/** Log in through the form as sample employee E-001 (נועה לוי). The login returns to her
 *  most recent conversation (left by an earlier test), so start a fresh one. */
async function loginAsNoa(): Promise<void> {
  fireEvent.change(await screen.findByLabelText("מספר עובד", {}, T), { target: { value: "E-001" } });
  fireEvent.change(screen.getByLabelText("קוד"), { target: { value: "1234" } });
  fireEvent.click(screen.getByRole("button", { name: "כניסה עם מספר עובד" }));
  await waitFor(() => expect(authSid()).toBeTruthy(), T);
  const resumed = authSid();
  fireEvent.click(await screen.findByRole("button", { name: "+ שיחה חדשה" }, T));
  await waitFor(() => expect(authSid()).not.toBe(resumed), T);
}

describe.skipIf(!health)("App ↔ backend", () => {
  it("the rep tickets are closed without the rep key", async () => {
    expect((await fetch(`${API_BASE}/api/rep/tickets`)).status).toBe(401);
  });

  it("CORS: the backend allows the Vite dev origin", async () => {
    const r = await fetch(`${API_BASE}/api/chat`, {
      method: "OPTIONS",
      headers: {
        Origin: "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
      },
    });
    expect(r.status).toBe(200);
    expect(r.headers.get("access-control-allow-origin")).toBe("http://localhost:5173");
  });

  it("the conversation routes refuse requests without a login token", async () => {
    const r = await fetch(`${API_BASE}/api/session/${"0".repeat(32)}`);
    expect(r.status).toBe(401);
  });

  it("a wrong code is refused on the login screen", async () => {
    render(<App />);
    fireEvent.change(await screen.findByLabelText("מספר עובד", {}, T), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("קוד"), { target: { value: "0000" } });
    fireEvent.click(screen.getByRole("button", { name: "כניסה עם מספר עובד" }));
    expect((await screen.findByRole("alert", {}, T)).textContent).toContain("מספר עובד או קוד שגויים");
    expect((screen.getByLabelText("מספר עובד") as HTMLInputElement).value).toBe("E-001");
    expect(screen.queryByLabelText("הודעה ליועץ")).toBeNull(); // still on the login screen
  });

  it("demo login E-001 preloads the record; all my options appear only after an answer", async () => {
    render(<App />);
    await loginAsNoa();
    // personal greeting from the preloaded record
    await screen.findByText(/שלום נועה!/, {}, T);
    expect(sessionStorage.getItem("car-advisor.auth")).toContain("token");
    // before any answer: only the invitation, not the ranked list
    await screen.findByTestId("all-options-request", {}, T);
    expect(screen.queryByTestId("all-options")).toBeNull();

    // no LLM involved: the invitation opens the ranking; pick the Atto 3 from it
    // (it is #7 for her - cheaper permitted cars and the waiver leave her more)
    fireEvent.click(screen.getByTestId("all-options-request"));
    fireEvent.click(await screen.findByRole("button", { name: /הצג את כל/ }, T));
    const atto = await screen.findByRole("button", { name: /^ATTO 3:/ }, T);
    expect(atto.getAttribute("aria-label")).toContain("+661 ₪");
    fireEvent.click(atto);
    const diff = await screen.findByTestId("net-diff", {}, T);
    await waitFor(() => expect(diff.textContent).toContain("+661 ₪"), T);
    expect(screen.getAllByText("11,032 ₪").length).toBeGreaterThan(0);
    expect(screen.getAllByText("11,693 ₪").length).toBeGreaterThan(0);
    expect(screen.getByText("החזר חשמל (ברוטו, מגולם)")).toBeTruthy();
    expect(screen.getByText("440 ₪")).toBeTruthy();
    // after the answer the heading compares with the checked option
    await screen.findByText("יש 6 אפשרויות שמשאירות לך יותר נטו", {}, T);
  });

  it.skipIf(!deterministic)(
    "after asking in the chat, the ranked options appear by themselves",
    async () => {
      render(<App />);
      await loginAsNoa();
      const box = await screen.findByLabelText("הודעה ליועץ", {}, T);
      await screen.findByText(/שלום נועה!/, {}, T);
      fireEvent.change(box, { target: { value: "אני רוצה לעבור לאטו 3" } });
      fireEvent.click(screen.getByRole("button", { name: "שליחה" }));
      await waitFor(() => expect(screen.getByTestId("net-diff").textContent).toContain("+661 ₪"), T);
      // no click needed: the answer brings the ranking
      await screen.findByTestId("all-options", {}, T);
      expect(screen.queryByTestId("all-options-request")).toBeNull();
    },
    30000,
  );

  it("the login survives a refresh, and logout returns to the login screen", async () => {
    const first = render(<App />);
    await loginAsNoa();
    await screen.findByText(/שלום נועה!/, {}, T);
    first.unmount(); // "refresh": a new App reads the token from sessionStorage
    render(<App />);
    await screen.findByText(/שלום נועה!/, {}, T);
    fireEvent.click(screen.getByRole("button", { name: "יציאה" }));
    await screen.findByRole("button", { name: "כניסה עם מספר עובד" }, T);
    expect(sessionStorage.getItem("car-advisor.auth")).toBeNull();
  });

  it.skipIf(!deterministic)("a new conversation keeps the earlier one, which can be reopened", async () => {
    render(<App />);
    await loginAsNoa();
    const box = await screen.findByLabelText("הודעה ליועץ", {}, T);
    await screen.findByText(/שלום נועה!/, {}, T);
    fireEvent.change(box, { target: { value: "אני רוצה לעבור לאטו 3" } });
    fireEvent.click(screen.getByRole("button", { name: "שליחה" }));
    await screen.findByTestId("net-diff", {}, T);
    const first = authSid();

    fireEvent.click(screen.getByRole("button", { name: "+ שיחה חדשה" }));
    await waitFor(() => expect(authSid()).not.toBe(first), T);
    await screen.findByText(/שלום נועה!/, {}, T);
    expect(screen.queryByTestId("net-diff")).toBeNull(); // a clean conversation

    fireEvent.click(screen.getByRole("button", { name: /השיחות שלי/ }));
    // newest first: earlier tests left older Atto 3 conversations further down
    const [earlier] = await screen.findAllByRole("button", { name: /מעבר ל-ATTO 3/ }, T);
    fireEvent.click(earlier);
    await waitFor(() => expect(authSid()).toBe(first), T);
    await screen.findByTestId("net-diff", {}, T); // its result is back
  }, 30000);

  it("quick switch recalculates for another allowed car", async () => {
    render(<App />);
    await loginAsNoa();
    fireEvent.click(await screen.findByTestId("all-options-request", {}, T));
    fireEvent.click(await screen.findByRole("button", { name: /הצג את כל/ }, T));
    fireEvent.click(await screen.findByRole("button", { name: /^ATTO 3:/ }, T));
    await screen.findByTestId("net-diff", {}, T);
    const select = (await screen.findByLabelText(
      "בחירת חלופה מתוך הרכבים המותרים", {}, T)) as HTMLSelectElement;
    await waitFor(() => expect(within(select).getAllByRole("option").length).toBeGreaterThan(3), T);
    expect(within(select).queryByText(/MODEL 3/)).toBeNull(); // group ד is not allowed
    fireEvent.change(select, { target: { value: "CAR-13" } });
    await waitFor(() => expect(screen.getAllByText("MAZDA 3 (2025)").length).toBeGreaterThan(0), T);
    expect(screen.getByTestId("net-diff").textContent).toContain("₪");
  });

  it.skipIf(!deterministic)(
    "fail-fast: a developer asking for a Tesla is told immediately, and picks an alternative",
    async () => {
      render(<App />);
      const box = await loginAsGuest();
      fireEvent.change(box, { target: { value: "אני מפתח תוכנה ורוצה לקחת טסלה מודל 3" } });
      fireEvent.click(screen.getByRole("button", { name: "שליחה" }));
      const blocked = await screen.findByTestId("blocked", {}, T);
      expect(blocked.textContent).toContain("מעל הקבוצה שלך");
      // both permitted electric cars are offered (closest to the Tesla request)
      const offered = within(blocked).getAllByRole("button", { name: /KONA EV|MG4/ });
      expect(offered.length).toBe(2);
      await waitFor(() => expect((offered[0] as HTMLButtonElement).disabled).toBe(false), T);
      fireEvent.click(offered[0]);
      await waitFor(() => expect(screen.queryByTestId("blocked")).toBeNull(), T);
      // now the (single-round) salary questionnaire starts
      await waitFor(() => expect(screen.getAllByText(/שכר בסיס/).length).toBeGreaterThan(0), T);
    },
    30000,
  );

  it.skipIf(!deterministic)(
    "payslip upload → review → confirm → one message about the cars → Excel E-004 result",
    async () => {
      const pdf = await (await fetch(e004Pdf)).arrayBuffer();
      render(<App />);
      await loginAsGuest();
      const input = (await screen.findByLabelText("קובץ תלוש שכר", {}, T)) as HTMLInputElement;
      const file = new File([pdf], "payslip.pdf", { type: "application/pdf" });
      Object.defineProperty(input, "files", { value: [file] });
      fireEvent.change(input);
      await waitFor(() => {
        // an upload error is shown as an alert - surface it instead of a bare "not found"
        expect(screen.queryAllByRole("alert").map((e) => e.textContent).join(" ")).toBe("");
        expect(screen.getByTestId("payslip-review")).toBeTruthy();
      }, T);
      const review = screen.getByTestId("payslip-review");
      expect((within(review).getByLabelText("ערך: שכר בסיס") as HTMLInputElement).value).toBe("16000");
      expect(review.textContent).not.toContain("123456782"); // the ID never comes back
      fireEvent.click(within(review).getByRole("button", { name: "אישור ושמירה" }));
      await screen.findByText(/אישרתי את הנתונים מתלוש השכר/, {}, T);

      const box = screen.getByLabelText("הודעה ליועץ");
      await waitFor(() => expect((box as HTMLTextAreaElement).disabled).toBe(false), T);
      fireEvent.change(box, { target: { value: "היום יש לי טוסון ואני רוצה לעבור ל-EV3" } });
      fireEvent.click(screen.getByRole("button", { name: "שליחה" }));
      await waitFor(() => expect(screen.getByTestId("net-diff").textContent).toContain("+1,207 ₪"), T);
    },
    30000,
  );

  it.skipIf(!deterministic)(
    "chat → freeze → rep answers on the rep screen → employee sees the new result",
    async () => {
      render(<App />);
      const box = await loginAsGuest();
      const send = async (text: string) => {
        fireEvent.change(box, { target: { value: text } });
        fireEvent.click(screen.getByRole("button", { name: "שליחה" }));
        await waitFor(() => expect(screen.queryByRole("status")).toBeNull(), T);
      };
      await send("שכר בסיס 16,000 ועמלות 3,000, אני מנהל אזור מכירות");
      await send("פנסיה 6%, השתלמות 2.5%, 2.25 נקודות זיכוי");
      await send("היום יש לי טוסון עם השתתפות 400");
      await send("אני רוצה לעבור ל-EV3 אבל סוכמה לי השתתפות מיוחדת ואני לא יודע כמה");
      await screen.findByText("⏳ ממתינים לתשובת נציג/ת שכר ותחבורה", {}, T);
      // in the meantime: today's net, the alternative waits for the answer
      expect((await screen.findByTestId("net-today", {}, T)).textContent).toContain("10,693 ₪");
      expect(screen.getByTestId("waiting-for-rep").textContent).toContain("EV3");
      expect(screen.queryByTestId("net-diff")).toBeNull();

      fireEvent.click(screen.getByRole("tab", { name: /מסך נציג/ }));
      // the rep screen is locked: a wrong key is refused, the right one opens it
      fireEvent.change(await screen.findByLabelText("מפתח נציג", {}, T), { target: { value: "wrong-key" } });
      fireEvent.click(screen.getByRole("button", { name: "כניסה" }));
      await screen.findByText("מפתח נציג חסר או שגוי", {}, T);
      fireEvent.change(screen.getByLabelText("מפתח נציג"), { target: { value: REP_KEY } });
      fireEvent.click(screen.getByRole("button", { name: "כניסה" }));
      // master-detail: open this conversation's ticket (the newest; older ones may be pending)
      const cards = await screen.findAllByRole("button", { name: /שיחה חופשית/ }, T);
      fireEvent.click(cards[cards.length - 1]);
      const input = await screen.findByLabelText(/השתתפות מאושרת עבור/, {}, T);
      fireEvent.change(input, { target: { value: "350" } });
      fireEvent.click(screen.getByRole("button", { name: "שליחה וסגירת הפנייה" }));
      await screen.findByText(/הפנייה נסגרה/, {}, T);

      fireEvent.click(screen.getByRole("tab", { name: "מסך עובד" }));
      // the employee screen polls while waiting; the result arrives without any action
      await waitFor(() => expect(screen.getByTestId("net-diff").textContent).toContain("+1,157 ₪"), {
        timeout: 15000,
      });
      expect(screen.queryByText("⏳ ממתינים לתשובת נציג/ת שכר ותחבורה")).toBeNull();
      expect(screen.getAllByText("11,850 ₪").length).toBeGreaterThan(0);
    },
    30000,
  );
});
