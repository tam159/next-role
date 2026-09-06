import { describe, expect, it } from "vitest";
import {
  CHART_SCHEMA_ID,
  chartEnvelopeSchema,
  formatValue,
  isChartPath,
  parseChartResult,
} from "@/app/lib/charts";

const envelope = {
  schema: CHART_SCHEMA_ID,
  title: "Runs per day",
  description: "",
  kind: "line",
  sql: "SELECT 1",
  row_count: 2,
  columns: ["day", "runs"],
  created_at: "2026-09-04T10:00:00+00:00",
  thread_id: "t-1",
  mapping: { x: "day", y: "runs" },
  figure: { data: [{ type: "scatter", x: [1], y: [2] }], layout: {} },
};

describe("isChartPath", () => {
  it("recognises a stored chart", () => {
    expect(isChartPath("/charts/t-1/runs.plotly.json")).toBe(true);
  });

  it("leaves other json alone", () => {
    // A report or an ordinary artifact must not be swallowed by the renderer.
    expect(isChartPath("/reports/t-1/summary.md")).toBe(false);
    expect(isChartPath("/upload/data.json")).toBe(false);
  });
});

describe("parseChartResult", () => {
  it("reads the tool's object result", () => {
    expect(parseChartResult({ path: "/charts/t/a.plotly.json", title: "A", kind: "bar" })).toEqual({
      path: "/charts/t/a.plotly.json",
      title: "A",
      kind: "bar",
    });
  });

  it("reads the same payload after it was serialised into history", () => {
    // Tool results reach a reopened thread as strings, not objects.
    const result = parseChartResult('{"path":"/charts/t/a.plotly.json","title":"A","kind":"bar"}');
    expect(result?.path).toBe("/charts/t/a.plotly.json");
  });

  it("accepts a published figure, whose row count is null", () => {
    // A figure built by a script has no query rows. The backend sends null and
    // the card must still parse it, or it falls back to showing raw JSON.
    const result = parseChartResult(
      '{"path":"/charts/t/a.plotly.json","title":"A","kind":"box","row_count":null,"columns":[]}'
    );

    expect(result?.kind).toBe("box");
    expect(result?.row_count).toBeNull();
  });

  it("returns null for the tool's error prose", () => {
    expect(parseChartResult("Error: column `nope` is not in the result.")).toBeNull();
  });

  it("returns null for JSON of the wrong shape", () => {
    expect(parseChartResult('{"ok":true}')).toBeNull();
  });
});

describe("chartEnvelopeSchema", () => {
  it("accepts a figure envelope", () => {
    expect(chartEnvelopeSchema.parse(envelope).figure?.data).toHaveLength(1);
  });

  it("accepts a table envelope", () => {
    const parsed = chartEnvelopeSchema.parse({
      ...envelope,
      kind: "table",
      figure: undefined,
      table: { columns: ["a"], rows: [["1"], [null]] },
    });
    expect(parsed.table?.rows[1][0]).toBeNull();
  });

  it("accepts a kpi envelope", () => {
    const parsed = chartEnvelopeSchema.parse({
      ...envelope,
      kind: "kpi",
      figure: undefined,
      kpi: { value: 42, label: "Runs", format: "number", previous: 40 },
    });
    expect(parsed.kpi?.value).toBe(42);
  });

  it("accepts an envelope with no row count", () => {
    const parsed = chartEnvelopeSchema.parse({ ...envelope, row_count: null });

    expect(parsed.row_count).toBeNull();
  });

  it("rejects an unknown schema version", () => {
    // The renderer must refuse a shape it does not understand rather than
    // guess at it.
    expect(() => chartEnvelopeSchema.parse({ ...envelope, schema: "nextrole.chart/v2" })).toThrow();
  });
});

describe("formatValue", () => {
  it("formats currency, percent and duration distinctly", () => {
    expect(formatValue(1234.5, "currency")).toBe("$1,234.5");
    expect(formatValue(0.0412, "percent")).toBe("4.1%");
    expect(formatValue(90, "duration_s")).toBe("1.5 min");
    expect(formatValue(12, "duration_s")).toBe("12.0 s");
  });

  it("renders a missing value as an em dash rather than NaN", () => {
    expect(formatValue(null, "number")).toBe("—");
    expect(formatValue(undefined, "currency")).toBe("—");
  });

  it("passes non-numeric values through", () => {
    expect(formatValue("n/a", "number")).toBe("n/a");
  });
});
