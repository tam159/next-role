"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertCircle, BarChart3, ChevronDown, Loader2 } from "lucide-react";
import { Collapse } from "@/app/components/Collapse";
import { PlotlyFigure } from "@/app/components/charts/PlotlyFigure";
import { ChartTable } from "@/app/components/charts/ChartTable";
import { KpiTile } from "@/app/components/charts/KpiTile";
import { fetchChartEnvelope, parseChartResult } from "@/app/lib/charts";
import type { ToolCall } from "@/app/types/types";
import { cn } from "@/lib/utils";

/**
 * A chart the analytics agent produced, rendered in the conversation.
 *
 * Everything it needs comes from the tool call and the stored file, never from
 * live stream events — which is what makes a reopened thread show its charts
 * again, since tool calls and results are rebuilt from message history.
 */
export function ChartCard({ toolCall }: { toolCall: ToolCall }) {
  const [sqlOpen, setSqlOpen] = useState(false);

  const pendingTitle =
    typeof toolCall.args["title"] === "string" ? toolCall.args["title"] : "Chart";
  const result = toolCall.result ? parseChartResult(toolCall.result) : null;
  // The tool reports failures as `Error: ...` prose, which never parses.
  const toolError = toolCall.result && !result ? String(toolCall.result).slice(0, 400) : null;

  const { data: envelope, error } = useSWR(
    result ? ["chart", result.path] : null,
    ([, path]) => fetchChartEnvelope(path),
    { revalidateOnFocus: false }
  );

  const title = envelope?.title ?? result?.title ?? pendingTitle;
  const isLoading = !toolCall.result || (result && !envelope && !error);

  return (
    <section className="w-full overflow-hidden rounded-[14px] border border-primary bg-surface-raised">
      <header className="flex items-start gap-2.5 border-b border-tertiary px-4 py-3">
        <BarChart3 className="mt-0.5 size-4 shrink-0 text-brand-accent" strokeWidth={1.8} />
        <div className="flex min-w-0 flex-col">
          <h3 className="truncate text-[14px] font-semibold text-primary">{title}</h3>
          {envelope?.description && (
            <p className="mt-0.5 text-xs leading-snug text-secondary">{envelope.description}</p>
          )}
        </div>
        {/* Null when the figure came from a script: there were no query rows,
            and "0 rows" would read as an empty result. */}
        {envelope?.row_count != null && (
          <span className="ml-auto shrink-0 pt-0.5 text-[11px] text-tertiary">
            {envelope.row_count.toLocaleString()} rows
          </span>
        )}
      </header>

      <div className="px-4 py-3">
        {isLoading && (
          <div className="flex h-[200px] items-center justify-center gap-2 text-sm text-tertiary">
            <Loader2 className="size-4 animate-spin" />
            {toolCall.result ? "Loading chart…" : "Building chart…"}
          </div>
        )}

        {toolError && (
          <div className="flex items-start gap-2 rounded-lg bg-surface3 px-3 py-2.5 text-[13px] text-secondary">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
            <span className="whitespace-pre-wrap">{toolError}</span>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-surface3 px-3 py-2.5 text-[13px] text-secondary">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
            <span>{(error as Error).message}</span>
          </div>
        )}

        {envelope?.figure && (
          <PlotlyFigure data={envelope.figure.data} layout={envelope.figure.layout} />
        )}
        {envelope?.table && (
          <ChartTable columns={envelope.table.columns} rows={envelope.table.rows} />
        )}
        {envelope?.kpi && (
          <KpiTile
            value={envelope.kpi.value}
            label={envelope.kpi.label}
            format={envelope.kpi.format}
            previous={envelope.kpi.previous}
          />
        )}
      </div>

      {envelope?.sql && (
        <div className="border-t border-tertiary">
          <button
            onClick={() => setSqlOpen((open) => !open)}
            aria-expanded={sqlOpen}
            className="flex w-full items-center gap-1.5 px-4 py-2 text-[12px] font-medium text-tertiary transition-colors hover:text-secondary"
          >
            <ChevronDown className={cn("size-3.5 transition-transform", sqlOpen && "rotate-180")} />
            SQL
          </button>
          <Collapse isExpanded={sqlOpen}>
            <pre className="m-0 overflow-x-auto px-4 pb-3 font-mono text-[12px] leading-relaxed whitespace-pre text-secondary">
              {envelope.sql}
            </pre>
          </Collapse>
        </div>
      )}
    </section>
  );
}
