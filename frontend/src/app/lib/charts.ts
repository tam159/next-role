import { z } from "zod";
import { filesApiUrl } from "@/app/lib/agentFiles";
import { authedFetch } from "@/lib/auth/token";

/**
 * The chart artifact the analytics agent writes.
 *
 * Charts are stored rather than streamed so they survive the run that made
 * them: reopening a thread re-renders every chart from its file. The envelope
 * carries the SQL and column mapping alongside the drawing, so a reader months
 * later can see what was actually measured.
 *
 * `figure` is a Plotly figure with no template — the viewer's theme is applied
 * at render time, so a chart drawn in light mode still reads correctly in dark.
 * `table` and `kpi` are drawn natively instead, which keeps the browser on
 * Plotly's small cartesian bundle and makes them match the rest of the UI.
 *
 * Mirrors `backend/agents/analytics_agent/charts.py`.
 */
export const CHART_SCHEMA_ID = "nextrole.chart/v1";

/** The analytics agent's chart tool. Its calls render as cards, not rail rows. */
export const CHART_TOOL_NAME = "create_chart";

export const chartEnvelopeSchema = z.object({
  schema: z.literal(CHART_SCHEMA_ID),
  title: z.string(),
  description: z.string().default(""),
  kind: z.string(),
  sql: z.string().default(""),
  row_count: z.number().nullish(),
  columns: z.array(z.string()).default([]),
  created_at: z.string().default(""),
  figure: z
    .object({
      data: z.array(z.record(z.string(), z.unknown())),
      layout: z.record(z.string(), z.unknown()).default({}),
    })
    .optional(),
  table: z
    .object({
      columns: z.array(z.string()),
      rows: z.array(z.array(z.string().nullable())),
    })
    .optional(),
  kpi: z
    .object({
      value: z.union([z.number(), z.string()]).nullable(),
      label: z.string(),
      format: z.string().default("number"),
      previous: z.union([z.number(), z.string()]).nullable().optional(),
    })
    .optional(),
});

export type ChartEnvelope = z.infer<typeof chartEnvelopeSchema>;

/** What `create_chart` returns to the model, and the card reads. */
export const chartResultSchema = z.object({
  path: z.string(),
  title: z.string(),
  kind: z.string(),
  row_count: z.number().nullish(),
  columns: z.array(z.string()).nullish(),
});

export type ChartResult = z.infer<typeof chartResultSchema>;

export function isChartPath(path: string): boolean {
  return path.endsWith(".plotly.json");
}

/**
 * Read a `create_chart` tool result, whatever shape it arrived in.
 *
 * Tool results reach the frontend as strings once serialised into message
 * history, so the same payload has to parse from an object or from JSON text.
 * Anything else (including the tool's `Error: ...` strings) yields null, and
 * the card shows the raw text instead.
 */
export function parseChartResult(result: unknown): ChartResult | null {
  const candidate =
    typeof result === "string"
      ? (() => {
          try {
            return JSON.parse(result);
          } catch {
            return null;
          }
        })()
      : result;
  const parsed = chartResultSchema.safeParse(candidate);
  return parsed.success ? parsed.data : null;
}

/** Fetch and validate a stored chart envelope through the files API. */
export async function fetchChartEnvelope(path: string): Promise<ChartEnvelope> {
  const response = await authedFetch(filesApiUrl(`/files/read?path=${encodeURIComponent(path)}`));
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "This chart's file is no longer in storage."
        : `Could not load the chart (${response.status}).`
    );
  }
  const body = (await response.json()) as { content: string; encoding: string };
  return chartEnvelopeSchema.parse(JSON.parse(body.content));
}

const FORMATTERS: Record<string, (value: number) => string> = {
  currency: (v) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
  percent: (v) => `${(v * 100).toFixed(1)}%`,
  duration_s: (v) => (v >= 60 ? `${(v / 60).toFixed(1)} min` : `${v.toFixed(1)} s`),
  number: (v) => v.toLocaleString(),
};

/** Format a KPI value the same way its axis counterpart would read. */
export function formatValue(value: number | string | null | undefined, format: string): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return (FORMATTERS[format] ?? FORMATTERS.number)(numeric);
}
