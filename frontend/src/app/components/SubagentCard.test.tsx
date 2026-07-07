import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AIMessageChunk } from "@langchain/core/messages";
import { useMessages, useToolCalls } from "@langchain/react";
import type { AnyStream, AssembledToolCall, SubagentDiscoverySnapshot } from "@langchain/react";
import { SubagentCard } from "@/app/components/SubagentCard";
import type { ToolCall } from "@/app/types/types";

// Keep everything real except the scoped selector hooks the card subscribes with.
vi.mock("@langchain/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@langchain/react")>();
  return { ...actual, useToolCalls: vi.fn(), useMessages: vi.fn() };
});

const stream = {} as AnyStream;

function makeSnapshot(overrides: Partial<SubagentDiscoverySnapshot> = {}) {
  return {
    id: "task-1",
    name: "researcher",
    namespace: ["task-1"],
    parentId: null,
    depth: 1,
    status: "running",
    taskInput: "Research Acme",
    output: undefined,
    error: undefined,
    startedAt: new Date("2026-07-01T10:00:00Z"),
    completedAt: null,
    ...overrides,
  } as SubagentDiscoverySnapshot;
}

const taskToolCall: ToolCall = {
  id: "task-1",
  name: "task",
  args: { subagent_type: "researcher", description: "Research Acme Corp" },
  status: "pending",
};

// The instantiation useToolCalls' mocked return type expects.
type CardToolCall = AssembledToolCall<string, Record<string, any>, never>;

function assembled(overrides: Partial<CardToolCall> = {}): CardToolCall {
  return {
    id: "nested-1",
    callId: "nested-1",
    name: "internet_search",
    namespace: ["task-1"],
    args: { query: "acme" },
    input: { query: "acme" },
    status: "finished",
    output: { type: "tool", content: "10 results" },
    error: undefined,
    ...overrides,
  } as CardToolCall;
}

function renderCard(snapshot = makeSnapshot()) {
  return render(<SubagentCard stream={stream} snapshot={snapshot} taskToolCall={taskToolCall} />);
}

beforeEach(() => {
  vi.mocked(useToolCalls).mockReturnValue([]);
  vi.mocked(useMessages).mockReturnValue([]);
});

describe("SubagentCard", () => {
  it.each([
    ["running", "Running"],
    ["complete", "Complete"],
    ["error", "Failed"],
  ] as const)("maps snapshot status %s to the %s indicator label", (status, label) => {
    renderCard(makeSnapshot({ status }));

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("renders the subagent name and the task description as the input", () => {
    renderCard();

    expect(screen.getByText("researcher")).toBeInTheDocument();
    // extractSubAgentContent prefers args.description for the Input panel.
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Research Acme Corp")).toBeInTheDocument();
  });

  it("renders the unwrapped tool payload in the output section", () => {
    const { container } = renderCard(
      makeSnapshot({
        status: "complete",
        output: { type: "tool", content: "Final research summary." },
        completedAt: new Date("2026-07-01T10:05:00Z"),
      })
    );

    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText("Final research summary.")).toBeInTheDocument();
    // The ToolMessage envelope must not leak into the panel.
    expect(container.textContent).not.toContain('"type"');
  });

  it("starts expanded and collapses/re-expands via the indicator", async () => {
    const user = userEvent.setup();
    renderCard();

    expect(screen.getByText("Input")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /researcher/ }));
    expect(screen.queryByText("Input")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /researcher/ }));
    expect(screen.getByText("Input")).toBeInTheDocument();
  });

  it("renders nested tool calls from the tools channel, excluding nested task calls", () => {
    vi.mocked(useToolCalls).mockReturnValue([
      assembled(),
      assembled({ id: "nested-2", callId: "nested-2", name: "task", status: "running" }),
    ]);
    renderCard();

    expect(screen.getByText("Activity")).toBeInTheDocument();
    expect(screen.getByText("internet_search")).toBeInTheDocument();
    // The nested "task" call is filtered out of the activity list.
    expect(screen.queryByText("task")).not.toBeInTheDocument();
  });

  it("shows still-streaming calls from message chunks without duplicating started ones", () => {
    vi.mocked(useToolCalls).mockReturnValue([assembled()]);
    vi.mocked(useMessages).mockReturnValue([
      new AIMessageChunk({
        content: "",
        tool_call_chunks: [
          // Already assembled on the tools channel — must not duplicate.
          {
            id: "nested-1",
            name: "internet_search",
            args: "{}",
            index: 0,
            type: "tool_call_chunk",
          },
          // Args still streaming — appears as a pending call.
          {
            id: "stream-1",
            name: "read_file",
            args: '{"path": "/pro',
            index: 1,
            type: "tool_call_chunk",
          },
        ],
      }),
    ]);
    const { container } = renderCard();

    expect(screen.getAllByText("internet_search")).toHaveLength(1);
    expect(screen.getByText("read_file")).toBeInTheDocument();
    // The streaming call renders in the pending state (spinner rail node).
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("falls back to the task tool call result when the snapshot has no output", () => {
    render(
      <SubagentCard
        stream={stream}
        snapshot={makeSnapshot({ status: "complete" })}
        taskToolCall={{ ...taskToolCall, status: "completed", result: "Task wrapped up." }}
      />
    );

    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText("Task wrapped up.")).toBeInTheDocument();
  });
});
