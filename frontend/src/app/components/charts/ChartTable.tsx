"use client";

/**
 * A result rendered as a table rather than a drawing.
 *
 * The agent picks this when the exact figures matter more than the shape. It
 * is deliberately not a Plotly table trace: drawn here it inherits the app's
 * type and colour, and the browser stays on Plotly's small cartesian bundle,
 * which carries no table trace at all.
 */
export function ChartTable({ columns, rows }: { columns: string[]; rows: (string | null)[][] }) {
  return (
    <div className="max-h-[360px] overflow-auto rounded-lg border border-primary">
      <table className="w-full border-collapse text-[13px]">
        <thead className="sticky top-0 bg-surface3">
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                className="border-b border-primary px-3 py-2 text-left font-semibold whitespace-nowrap text-secondary"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="even:bg-surface2">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="border-b border-tertiary px-3 py-1.5 whitespace-nowrap text-primary"
                >
                  {cell ?? <span className="text-tertiary">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
