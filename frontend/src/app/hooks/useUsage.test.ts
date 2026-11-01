import { blockedMessage, quotaRefusalMessage } from "./useUsage";

describe("quotaRefusalMessage", () => {
  it("returns the server's detail for a 429", () => {
    const error = Object.assign(new Error("HTTP 429"), {
      status: 429,
      text: JSON.stringify({ detail: "You've used all 10 runs for the last 24 hours." }),
    });

    expect(quotaRefusalMessage(error)).toBe("You've used all 10 runs for the last 24 hours.");
  });

  it("falls back to the raw body when it is not JSON", () => {
    expect(quotaRefusalMessage({ status: 429, text: "Too many runs" })).toBe("Too many runs");
  });

  it("reads the v2 protocol's run.start refusal", () => {
    const error = Object.assign(new Error("429: You've used all 2 runs for the last 24 hours."), {
      name: "ProtocolError",
      code: "unknown_error",
    });

    expect(quotaRefusalMessage(error)).toBe("You've used all 2 runs for the last 24 hours.");
  });

  it("ignores every other failure", () => {
    expect(quotaRefusalMessage({ status: 500, text: "boom" })).toBeNull();
    expect(quotaRefusalMessage(new Error("AbortError"))).toBeNull();
    expect(quotaRefusalMessage(undefined)).toBeNull();
  });
});

describe("blockedMessage", () => {
  it("names the window and the local reset time", () => {
    const resetsAt = new Date(Date.now() + 2 * 3_600_000);

    const message = blockedMessage({
      enabled: true,
      blocked: true,
      reason: "usd_week",
      resets_at: resetsAt.toISOString(),
    });

    expect(message).toMatch(/^You've used your usage allowance for the last 7 days\./);
    expect(message).toContain("You can start a new run after");
  });

  it("works without a reset time", () => {
    expect(blockedMessage({ enabled: true, blocked: true, reason: "global_usd_day" })).toBe(
      "NextRole has reached its shared daily capacity."
    );
  });
});
