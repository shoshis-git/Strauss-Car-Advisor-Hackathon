import { describe, expect, it } from "vitest";
import { boldSegments, ils, signedIls, trend } from "./format";

describe("format", () => {
  it("formats shekels with thousands separators", () => {
    expect(ils(11032)).toBe("11,032 ₪");
    expect(ils(null)).toBe("—");
  });
  it("signs the difference explicitly", () => {
    expect(signedIls(661)).toBe("+661 ₪");
    expect(signedIls(-3522)).toBe("-3,522 ₪");
    expect(signedIls(0)).toBe("0 ₪");
  });
  it("classifies the trend", () => {
    expect([trend(5), trend(-5), trend(0), trend(null)]).toEqual(["up", "down", "flat", "flat"]);
  });
  it("splits **bold** segments", () => {
    expect(boldSegments("a **b** c")).toEqual([
      { text: "a ", bold: false },
      { text: "b", bold: true },
      { text: " c", bold: false },
    ]);
  });
});
