"use client";

import useSWR from "swr";
import { AlertCircle } from "lucide-react";
import { PlotlyFigure } from "@/app/components/charts/PlotlyFigure";
import { ChartTable } from "@/app/components/charts/ChartTable";
import { KpiTile } from "@/app/components/charts/KpiTile";
import { fetchChartEnvelope } from "@/app/lib/charts";

/**
 * A stored chart rendered from its path alone.
 *
 * Used where a chart is referenced rather than produced: a saved report embeds
 * one as `![Title](/charts/<thread>/<slug>.plotly.json)`, and the file viewer
 * opens one directly. The card version (`ChartCard`) adds the tool-call
 * framing; this is just the drawing.
 */
export function InlineChart({ path, alt }: { path: string; alt?: string }) {
  const { data: envelope, error } = useSWR(["chart", path], ([, p]) => fetchChartEnvelope(p), {
    revalidateOnFocus: false,
  });

  if (error) {
    return (
      <div className="my-4 flex items-start gap-2 rounded-lg border border-primary bg-surface3 px-3 py-2.5 text-[13px] text-secondary">
        <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
        <span>{(error as Error).message}</span>
      </div>
    );
  }

  if (!envelope) {
    return <div className="my-4 h-[240px] animate-pulse rounded-lg bg-surface3" />;
  }

  return (
    <figure className="my-4">
      {envelope.figure && (
        <PlotlyFigure data={envelope.figure.data} layout={envelope.figure.layout} />
      )}
      {envelope.table && <ChartTable columns={envelope.table.columns} rows={envelope.table.rows} />}
      {envelope.kpi && (
        <KpiTile
          value={envelope.kpi.value}
          label={envelope.kpi.label}
          format={envelope.kpi.format}
          previous={envelope.kpi.previous}
        />
      )}
      <figcaption className="mt-1.5 text-center text-xs text-tertiary">
        {alt || envelope.title}
      </figcaption>
    </figure>
  );
}
