import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Comparison, Options, StateView } from "../api";
import { buildRows, ComparisonDashboard } from "./ComparisonDashboard";

afterEach(cleanup);

// E-001 as returned by the backend (values from the reference sheet)
const E001: Comparison = {
  ok: true, is_eligible: true, reason: "הרכב מהקבוצה שלך או נמוכה ממנה",
  current_label: "COROLLA CROSS (2025)", alternative_label: "ATTO 3 (2025)",
  net_today: 11032, net_alt: 11693, net_diff: 661, tax_today: 3443, tax_alt: 3283,
  btl_health_today: 2153, btl_health_alt: 2092, electric_gross: 440, family_gap_deduction: 0,
  explanation: "שווי שימוש 4,070 → 3,130 ₪ · +250 ₪ נטו החזר חשמל (מגולם)",
  today: { label: "COROLLA CROSS (2025)", kind: "car", car_id: "CAR-09", is_electric: false,
    value_of_use: 4070, participation: 300, maintenance_allowance: 0, electric_gross: 0,
    family_gap_deduction: 0, income_tax: 3443, btl_health: 2153, net: 11032 },
  alternative: { label: "ATTO 3 (2025)", kind: "car", car_id: "CAR-10", is_electric: true,
    value_of_use: 3130, participation: 300, maintenance_allowance: 0, electric_gross: 440,
    family_gap_deduction: 0, income_tax: 3283, btl_health: 2092, net: 11693 },
};

function stateWith(c: Comparison | null): StateView {
  return {
    display_name: "נועה לוי (E-001)",
    profile: { eligibility_group: "ג", occupation: "מנהל/ת מוצר", base_salary: 18500,
      commissions_bonuses: 0, pension_pct: 6, hishtalmut_pct: 2.5, credit_points: 2.75, children_count: 2 },
    current: { status: "car", current_car_code: "CAR-09", current_participation: 300, current_maintenance: null },
    target: { target_action: "replace", target_car_code: "CAR-10", target_participation: null },
    missing_info: [], missing_labels: [], waiting_for_rep: false, rep_question: null,
    last_comparison: c,
  };
}

const OPTIONS: Options = {
  ok: true, group: "ג", budget: 4500,
  allowed_cars: [
    { car_id: "CAR-09", model_name: "COROLLA CROSS", manufacturer: "", group: "ג", drive: "היברידי",
      registration_year: 2025, value_of_use: 4070, default_participation: 300 },
    { car_id: "CAR-13", model_name: "MAZDA 3", manufacturer: "", group: "ג", drive: "בנזין",
      registration_year: 2025, value_of_use: 4570, default_participation: 350 },
  ],
  family_track: { eligible: false, cars: [], reason: "..." },
  waiver: { allowed: true, reason: "העיסוק מאפשר ויתור", maintenance: 2000 },
};

describe("ComparisonDashboard", () => {
  it("shows the bottom line big, with a green positive difference", () => {
    render(<ComparisonDashboard state={stateWith(E001)} options={OPTIONS} busy={false} onSwitch={() => {}} />);
    // each net appears twice: big number + the table's bottom row
    expect(screen.getAllByText("11,032 ₪")).toHaveLength(2);
    expect(screen.getAllByText("11,693 ₪")).toHaveLength(2);
    const diff = screen.getByTestId("net-diff");
    expect(diff.textContent).toContain("+661 ₪");
    expect(diff.className).toContain("emerald");
    expect(screen.getByText(E001.explanation)).toBeTruthy();
  });

  it("renders red for a lower net", () => {
    const worse = { ...E001, net_alt: 10000, net_diff: -1032 };
    render(<ComparisonDashboard state={stateWith(worse)} options={OPTIONS} busy={false} onSwitch={() => {}} />);
    const diff = screen.getByTestId("net-diff");
    expect(diff.textContent).toContain("-1,032 ₪");
    expect(diff.className).toContain("red");
  });

  it("builds the line-by-line table incl. electric refund and taxes", () => {
    const rows = buildRows(E001).map((r) => [r.label, r.today, r.alt]);
    expect(rows).toEqual([
      ["רכב / סטטוס", "COROLLA CROSS (2025)", "ATTO 3 (2025)"],
      ["שווי שימוש חודשי (למס)", "4,070 ₪", "3,130 ₪"],
      ["דמי השתתפות עובד", "300 ₪", "300 ₪"],
      ["החזר חשמל (ברוטו, מגולם)", "0 ₪", "440 ₪"],
      ["מס הכנסה", "3,443 ₪", "3,283 ₪"],
      ["ביטוח לאומי ומס בריאות", "2,153 ₪", "2,092 ₪"],
      ["נטו לחודש", "11,032 ₪", "11,693 ₪"],
    ]);
  });

  it("adds waiver maintenance and family-gap rows only when relevant", () => {
    const waiver = { ...E001, alternative: { ...E001.alternative!, kind: "waiver" as const, is_electric: false,
      label: "ויתור", value_of_use: 0, participation: 0, electric_gross: 0, maintenance_allowance: 2000 } };
    const labels = buildRows(waiver).map((r) => r.label);
    expect(labels).toContain("אחזקת רכב בוויתור");
    expect(labels).not.toContain("החזר חשמל (ברוטו, מגולם)");
    const family = { ...E001, alternative: { ...E001.alternative!, family_gap_deduction: 1150 } };
    expect(buildRows(family).map((r) => r.label)).toContain("חלק העובד בפער מסלול משפחתי");
  });

  it("explains an ineligible alternative instead of numbers", () => {
    const bad = { ...E001, is_eligible: false, net_alt: null, net_diff: null, alternative: null,
      reason: "הרכב MODEL 3 מקבוצה ד - מעל הקבוצה שלך (ג)" };
    render(<ComparisonDashboard state={stateWith(bad)} options={OPTIONS} busy={false} onSwitch={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("מעל הקבוצה שלך");
    expect(screen.queryByTestId("net-diff")).toBeNull();
  });

  it("quick switch lists allowed cars (minus the current one) and waiver", () => {
    const onSwitch = vi.fn();
    render(<ComparisonDashboard state={stateWith(E001)} options={OPTIONS} busy={false} onSwitch={onSwitch} />);
    const select = screen.getByLabelText("בחירת חלופה מתוך הרכבים המותרים") as HTMLSelectElement;
    const values = within(select).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["", "CAR-13", "waiver"]); // CAR-09 is the current car
    fireEvent.change(select, { target: { value: "CAR-13" } });
    expect(onSwitch).toHaveBeenCalledWith({ car_id: "CAR-13" });
    fireEvent.change(select, { target: { value: "waiver" } });
    expect(onSwitch).toHaveBeenCalledWith({ waiver: true });
  });

  it("fail-fast: a blocked request shows the reason and clickable permitted alternatives", () => {
    const onSwitch = vi.fn();
    const s: StateView = {
      ...stateWith(null),
      profile: { ...stateWith(null).profile, base_salary: null, eligibility_group: "ב" },
      missing_info: ["allowed_alternative"], missing_labels: ["חלופה מותרת"],
      precheck: {
        eligible: false, requested: "MODEL 3 (CAR-14, קבוצה ד)",
        reason: "הרכב MODEL 3 מקבוצה ד - מעל הקבוצה שלך (ב)",
        alternatives: [{ car_id: "CAR-08", model_name: "KONA EV", manufacturer: "", group: "ב",
          drive: "חשמלי", registration_year: 2026, value_of_use: 2960, default_participation: 250 }],
      },
    };
    render(<ComparisonDashboard state={s} options={null} busy={false} onSwitch={onSwitch} />);
    expect(screen.getByTestId("blocked").textContent).toContain("מעל הקבוצה שלך");
    fireEvent.click(screen.getByRole("button", { name: /KONA EV/ }));
    expect(onSwitch).toHaveBeenCalledWith({ car_id: "CAR-08" });
    // while blocked, empty salary fields are genuinely missing
    expect(screen.getAllByText("חסר").length).toBeGreaterThan(0);
  });

  it("empty fields not needed for the calculation show 'לא נדרש', not 'חסר'", () => {
    const s = { ...stateWith(null),
      profile: { ...stateWith(null).profile, children_count: null }, missing_info: [] };
    render(<ComparisonDashboard state={s} options={null} busy={false} onSwitch={() => {}} />);
    const children = screen.getByText("ילדים עד 23").parentElement!;
    expect(children.textContent).toContain("לא נדרש");
    expect(children.textContent).not.toContain("חסר");
  });

  it("shows the collected profile and what is still missing", () => {
    const s = { ...stateWith(null), missing_info: ["credit_points"], missing_labels: ["נקודות זיכוי"] };
    render(<ComparisonDashboard state={s} options={null} busy={false} onSwitch={() => {}} />);
    expect(screen.getByText("18,500 ₪")).toBeTruthy();
    expect(screen.getByText(/עוד חסר לחישוב/).parentElement?.textContent).toContain("נקודות זיכוי");
  });
});
