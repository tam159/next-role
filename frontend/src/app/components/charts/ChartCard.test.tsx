import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import { ChartCard } from "@/app/components/charts/ChartCard";
import { CHART_SCHEMA_ID } from "@/app/lib/charts";
import type { ToolCall } from "@/app/types/types";

// Plotly is loaded dynamically and touches the DOM at import; the card's own
// behaviour is what matters here.
vi.mock("@/app/components/charts/PlotlyFigure", () => ({
  PlotlyFigure: ({ data }: { data: unknown[] }) => (
    <div data-testid="plotly" data-traces={data.length} />
  ),
}));

const envelope = {
  schema: CHART_SCHEMA_ID,
  title: "Runs per day",
  description: "Interrupted runs are approval pauses, not failures.",
  kind: "line",
  sql: "SELECT run_date, count() FROM nextrole_marts.fct_run GROUP BY run_date",
  row_count: 17,
  columns: ["run_date", "runs"],
  created_at: "2026-09-04T10:00:00+00:00",
  figure: { data: [{ type: "scatter" }], layout: {} },
};

function call(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    id: "call-1",
    name: "create_chart",
    args: { title: "Runs per day", kind: "line" },
    status: "completed",
    result: JSON.stringify({
      path: "/charts/t-1/runs-per-day.plotly.json",
      title: "Runs per day",
      kind: "line",
      row_count: 17,
    }),
    ...overrides,
  };
}

function renderCard(toolCall: ToolCall) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ChartCard toolCall={toolCall} />
    </SWRConfig>
  );
}

function stubFetch(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => ({ content: JSON.stringify(body), encoding: "utf-8" }),
    })
  );
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", "http://deploy:2024");
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", "career_agent");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("ChartCard", () => {
  it("shows the title from the args while the chart is still being built", () => {
    stubFetch(envelope);
    renderCard(call({ result: undefined, status: "pending" }));

    expect(screen.getByText("Runs per day")).toBeInTheDocument();
    expect(screen.getByText(/Building chart/)).toBeInTheDocument();
  });

  it("renders the figure, its caveat and the row count once loaded", async () => {
    stubFetch(envelope);
    renderCard(call());

    await waitFor(() => expect(screen.getByTestId("plotly")).toBeInTheDocument());
    expect(screen.getByText(/approval pauses/)).toBeInTheDocument();
    expect(screen.getByText("17 rows")).toBeInTheDocument();
  });

  it("reveals the SQL on request, so a number can be checked", async () => {
    stubFetch(envelope);
    renderCard(call());
    await waitFor(() => expect(screen.getByTestId("plotly")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /sql/i }));

    expect(screen.getByText(/GROUP BY run_date/)).toBeInTheDocument();
  });

  it("renders a table envelope natively instead of a figure", async () => {
    stubFetch({
      ...envelope,
      kind: "table",
      figure: undefined,
      table: { columns: ["status", "runs"], rows: [["success", "235"]] },
    });
    renderCard(call());

    await waitFor(() => expect(screen.getByText("success")).toBeInTheDocument());
    expect(screen.queryByTestId("plotly")).not.toBeInTheDocument();
  });

  it("renders a kpi envelope as a single number with its comparison", async () => {
    stubFetch({
      ...envelope,
      kind: "kpi",
      figure: undefined,
      kpi: { value: 235, label: "Successful runs", format: "number", previous: 200 },
    });
    renderCard(call());

    await waitFor(() => expect(screen.getByText("235")).toBeInTheDocument());
    expect(screen.getByText(/17.5%/)).toBeInTheDocument();
  });

  it("renders a script-published figure and omits the row count", async () => {
    // The fallback publishes a box plot with no query rows behind it.
    stubFetch({ ...envelope, kind: "box", row_count: null });
    renderCard(
      call({
        args: { title: "Duration by status", kind: "box" },
        result: JSON.stringify({
          path: "/charts/t-1/duration.plotly.json",
          title: "Duration by status",
          kind: "box",
          row_count: null,
        }),
      })
    );

    await waitFor(() => expect(screen.getByTestId("plotly")).toBeInTheDocument());
    expect(screen.queryByText(/rows$/)).not.toBeInTheDocument();
  });

  it("surfaces the tool's own error text rather than a blank card", () => {
    stubFetch(envelope);
    renderCard(call({ result: "Error: column `nope` is not in the result.", status: "error" }));

    expect(screen.getByText(/column `nope`/)).toBeInTheDocument();
  });

  it("explains a chart whose file has gone missing", async () => {
    stubFetch(null, false, 404);
    renderCard(call());

    await waitFor(() => expect(screen.getByText(/no longer in storage/)).toBeInTheDocument());
  });
});
