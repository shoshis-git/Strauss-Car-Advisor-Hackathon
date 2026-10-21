const nf = new Intl.NumberFormat("he-IL", { maximumFractionDigits: 2 });

/** 11032 -> "11,032" */
export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : nf.format(n);
}

/** 11032 -> "11,032 ₪" */
export function ils(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${nf.format(n)} ₪`;
}

/** 661 -> "+661 ₪", -3522 -> "-3,522 ₪", 0 -> "0 ₪" */
export function signedIls(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}${nf.format(Math.abs(n))} ₪`;
}

export type Trend = "up" | "down" | "flat";

export function trend(n: number | null | undefined): Trend {
  if (!n) return "flat";
  return n > 0 ? "up" : "down";
}

/** Minimal rich text for bot messages: **bold** segments (LLMs often emit them). */
export function boldSegments(text: string): { text: string; bold: boolean }[] {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part) =>
      part.startsWith("**") && part.endsWith("**")
        ? { text: part.slice(2, -2), bold: true }
        : { text: part, bold: false },
    );
}

export function timeOf(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}
