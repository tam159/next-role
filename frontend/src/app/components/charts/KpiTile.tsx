"use client";

import { formatValue } from "@/app/lib/charts";

/**
 * One headline number.
 *
 * A single figure does not need a chart, but it does deserve emphasis. When
 * the query returned a second row, it is shown as the comparison — the usual
 * "this week against last".
 */
export function KpiTile({
  value,
  label,
  format,
  previous,
}: {
  value: number | string | null;
  label: string;
  format: string;
  previous?: number | string | null;
}) {
  const current = typeof value === "number" ? value : Number(value);
  const prior = typeof previous === "number" ? previous : Number(previous);
  const comparable = Number.isFinite(current) && Number.isFinite(prior) && prior !== 0;
  const change = comparable ? (current - prior) / Math.abs(prior) : null;

  return (
    <div className="flex flex-col gap-1 px-1 py-4">
      <span className="font-serif text-[40px] leading-none font-semibold text-primary">
        {formatValue(value, format)}
      </span>
      <span className="text-[13px] text-secondary">{label}</span>
      {change !== null && (
        <span className="text-xs text-tertiary">
          {change >= 0 ? "▲" : "▼"} {Math.abs(change * 100).toFixed(1)}% vs{" "}
          {formatValue(previous, format)}
        </span>
      )}
    </div>
  );
}
