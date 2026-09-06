"use client";

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { themedLayout } from "@/app/components/charts/chartTheme";

/**
 * Plotly, loaded only when a chart is actually on screen.
 *
 * `ssr: false` because Plotly touches `window` at import; the dynamic import
 * keeps its ~1.5 MB out of the initial bundle for the many sessions that never
 * ask for a chart. The package is the cartesian build, aliased to `plotly.js`
 * so `react-plotly.js` resolves it as its peer.
 */
const Plot = dynamic(
  async () => {
    const [{ default: createPlotlyComponent }, { default: Plotly }] = await Promise.all([
      import("react-plotly.js/factory"),
      import("plotly.js"),
    ]);
    return createPlotlyComponent(Plotly);
  },
  { ssr: false, loading: () => <div className="h-[320px] animate-pulse rounded-lg bg-surface3" /> }
);

interface PlotlyFigureProps {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  height?: number;
}

export function PlotlyFigure({ data, layout, height = 320 }: PlotlyFigureProps) {
  const { resolvedTheme } = useTheme();

  // Re-themed on every theme change: the stored envelope carries no colours, so
  // one chart reads correctly in both light and dark.
  const themed = useMemo(
    () => ({ ...themedLayout(layout, resolvedTheme === "dark"), height }),
    [layout, resolvedTheme, height]
  );

  return (
    <Plot
      data={data as Plotly.Data[]}
      layout={themed as Partial<Plotly.Layout>}
      config={{ displaylogo: false, responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: "100%", height: `${height}px` }}
    />
  );
}
