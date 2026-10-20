import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatPanel } from "./ChatPanel";

afterEach(cleanup);

const noop = () => {};

describe("ChatPanel", () => {
  it("tells the employee when the LLM fell back to deterministic mode", () => {
    render(
      <ChatPanel items={[]} state={null} busy={false} error={null}
        notice="מודל השפה אינו זמין כרגע" onSend={noop} onUpload={noop} />,
    );
    expect(screen.getByTestId("llm-notice").textContent).toContain("מודל השפה אינו זמין");
  });

  it("an error replaces the notice", () => {
    render(
      <ChatPanel items={[]} state={null} busy={false} error="השרת אינו זמין"
        notice="מודל השפה אינו זמין" onSend={noop} onUpload={noop} />,
    );
    expect(screen.queryByTestId("llm-notice")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("השרת אינו זמין");
  });
});
