import { describe, expect, it } from "vitest";
import { themedLayout } from "@/app/components/charts/chartTheme";

describe("themedLayout", () => {
  it("keeps the axis configuration the agent chose", () => {
    // The stored layout owns structure (titles, formats); the viewer owns
    // only colour and type.
    const themed = themedLayout(
      { yaxis: { tickformat: "$,.2f", title: { text: "cost" } }, barmode: "stack" },
      false
    );

    expect((themed.yaxis as Record<string, unknown>).tickformat).toBe("$,.2f");
    expect((themed.yaxis as Record<string, unknown>).title).toEqual({ text: "cost" });
    expect(themed.barmode).toBe("stack");
  });

  it("gives each axis its own object", () => {
    // Plotly normalizes layout in place: a shared object lets the x axis's
    // inferred `type: "date"` leak onto the y axis and render counts as times.
    const themed = themedLayout({}, false);

    expect(themed.xaxis).not.toBe(themed.yaxis);
  });

  it("paints a transparent canvas so the card surface shows through", () => {
    const themed = themedLayout({}, false);

    expect(themed.paper_bgcolor).toBe("rgba(0,0,0,0)");
    expect(themed.plot_bgcolor).toBe("rgba(0,0,0,0)");
  });

  it("uses a different hover surface in dark mode", () => {
    const light = themedLayout({}, false).hoverlabel as Record<string, unknown>;
    const dark = themedLayout({}, true).hoverlabel as Record<string, unknown>;

    expect(light.bgcolor).not.toBe(dark.bgcolor);
  });
});
