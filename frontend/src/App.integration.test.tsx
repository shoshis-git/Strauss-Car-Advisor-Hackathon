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

  it("loading demo E-001 shows the reference numbers and the detailed table", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /נועה לוי/ }, T));
    const diff = await screen.findByTestId("net-diff", {}, T);
    expect(diff.textContent).toContain("+661 ₪");
    expect(screen.getAllByText("11,032 ₪").length).toBeGreaterThan(0);
    expect(screen.getAllByText("11,693 ₪").length).toBeGreaterThan(0);
    expect(screen.getByText("החזר חשמל (ברוטו, מגולם)")).toBeTruthy();
    expect(screen.getByText("440 ₪")).toBeTruthy();
    // the original question is in the chat in the same render as the numbers
    expect(screen.getByText(/אם אעבור מהקורולה קרוס/)).toBeTruthy();
  });

  it("quick switch recalculates for another allowed car", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /נועה לוי/ }, T));
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
      const box = await screen.findByLabelText("הודעה ליועץ");
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
      const box = await screen.findByLabelText("הודעה ליועץ");
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

      fireEvent.click(screen.getByRole("tab", { name: /מסך נציג/ }));
      // the rep screen is locked: a wrong key is refused, the right one opens it
      fireEvent.change(await screen.findByLabelText("מפתח נציג", {}, T), { target: { value: "wrong-key" } });
      fireEvent.click(screen.getByRole("button", { name: "כניסה" }));
      await screen.findByText("מפתח נציג חסר או שגוי", {}, T);
      fireEvent.change(screen.getByLabelText("מפתח נציג"), { target: { value: REP_KEY } });
      fireEvent.click(screen.getByRole("button", { name: "כניסה" }));
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
