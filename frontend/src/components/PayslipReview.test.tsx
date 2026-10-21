import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PayslipExtraction } from "../api";
import { PayslipReview } from "./PayslipReview";

afterEach(cleanup);

const EXTRACTION: PayslipExtraction = {
  fields: [
    { field: "occupation", label: "העיסוק", value: "מנהל/ת אזור מכירות", evidence: "תפקיד: מנהל אזור מכירות" },
    { field: "base_salary", label: "שכר בסיס", value: 16000, evidence: "16,000.00 שכר בסיס" },
    { field: "pension_pct", label: "אחוז הפרשת עובד לפנסיה", value: 6, evidence: "1,140.00 %6.00 קופת גמל" },
  ],
  not_found: ["נקודות זיכוי"],
  rejected: ["אחוז הפרשת עובד לקרן השתלמות: הערך 400 לא נמצא בתלוש"],
};

function setup() {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<PayslipReview extraction={EXTRACTION} busy={false} onConfirm={onConfirm} onCancel={onCancel} />);
  return { onConfirm, onCancel };
}

describe("PayslipReview", () => {
  it("shows each value next to the payslip line it came from, plus what is missing", () => {
    setup();
    expect((screen.getByLabelText("ערך: שכר בסיס") as HTMLInputElement).value).toBe("16000");
    expect(screen.getByText("״16,000.00 שכר בסיס״")).toBeTruthy();
    expect(screen.getByText(/לא נמצאו בתלוש.*נקודות זיכוי/)).toBeTruthy();
    expect(screen.getByText(/לא אומתו מול התלוש/)).toBeTruthy();
    expect(screen.getByText(/הושחרו לפני העיבוד/)).toBeTruthy();
  });

  it("sends only confirmed values, with the employee's corrections", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByLabelText("ערך: שכר בסיס"), { target: { value: "16,500" } });
    fireEvent.click(screen.getByLabelText("לכלול: אחוז הפרשת עובד לפנסיה"));
    fireEvent.click(screen.getByRole("button", { name: "אישור ושמירה" }));
    expect(onConfirm).toHaveBeenCalledWith({ occupation: "מנהל/ת אזור מכירות", base_salary: 16500 });
  });

  it("blocks invalid values and an empty selection", () => {
    setup();
    const confirm = screen.getByRole("button", { name: "אישור ושמירה" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("ערך: שכר בסיס"), { target: { value: "abc" } });
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText("יש ערך לא תקין")).toBeTruthy();
    for (const label of ["העיסוק", "שכר בסיס", "אחוז הפרשת עובד לפנסיה"]) {
      fireEvent.click(screen.getByLabelText(`לכלול: ${label}`));
    }
    expect(confirm.disabled).toBe(true); // nothing selected
  });

  it("cancel discards the proposal", () => {
    const { onCancel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "ביטול" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
