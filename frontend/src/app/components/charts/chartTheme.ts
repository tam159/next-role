/**
 * Map the app's design tokens onto a Plotly layout.
 *
 * Charts are stored without colours so the same file reads correctly in light
 * and dark, and so a palette change here reaches every chart ever drawn rather
 * than only new ones. Values are read from the live CSS custom properties, so
 * a user's accent choice is reflected too.
 */

const FALLBACK = {
  light: { ink: "#211f1a", muted: "#6b6459", grid: "#e7e1d4" },
  dark: { ink: "#ede9e0", muted: "#9c948a", grid: "#3a3630" },
};

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function axisTheme(grid: string, muted: string): Record<string, unknown> {
  // A fresh object per axis: Plotly normalizes layout in place, so a shared
  // one lets the x axis's inferred `type: "date"` leak onto the y axis and
  // render counts as timestamps.
  return {
    gridcolor: grid,
    zerolinecolor: grid,
    linecolor: grid,
    tickfont: { color: muted, size: 11 },
    automargin: true,
  };
}

/**
 * Apply the viewer's theme to a stored figure's layout.
 *
 * Merged rather than replaced: the stored layout carries the structure the
 * agent chose — axis titles, tick formats, bar mode, hover mode — and only the
 * colours and fonts belong to the viewer.
 */
export function themedLayout(
  layout: Record<string, unknown>,
  isDark: boolean
): Record<string, unknown> {
  const base = isDark ? FALLBACK.dark : FALLBACK.light;
  const ink = cssVar("--color-text-primary", base.ink);
  const muted = cssVar("--color-text-tertiary", base.muted);
  const grid = cssVar("--color-border", base.grid);

  const mergeAxis = (existing: unknown): Record<string, unknown> => ({
    ...axisTheme(grid, muted),
    ...((existing as Record<string, unknown>) ?? {}),
    tickfont: { color: muted, size: 11 },
  });

  return {
    ...layout,
    // Transparent so the card's own surface shows through in both themes.
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: {
      color: ink,
      family: "var(--font-sans), ui-sans-serif, system-ui, sans-serif",
      size: 12,
    },
    legend: {
      ...((layout.legend as Record<string, unknown>) ?? {}),
      font: { color: muted, size: 11 },
    },
    hoverlabel: {
      bgcolor: isDark ? "#2a2620" : "#ffffff",
      bordercolor: grid,
      font: { color: ink, family: "var(--font-mono), ui-monospace, monospace", size: 11 },
    },
    xaxis: mergeAxis(layout.xaxis),
    yaxis: mergeAxis(layout.yaxis),
  };
}
