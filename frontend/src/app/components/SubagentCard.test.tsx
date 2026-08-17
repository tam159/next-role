import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AIMessage, AIMessageChunk } from "@langchain/core/messages";
import { useMessages, useToolCalls } from "@langchain/react";
import type { AnyStream, AssembledToolCall, SubagentDiscoverySnapshot } from "@langchain/react";
import { QueuedSubagentCard, SubagentCard } from "@/app/components/SubagentCard";
import type { ApprovalsBundle } from "@/app/hooks/useInterruptApprovals";
import type { PendingApproval, ToolCall } from "@/app/types/types";

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

function renderCard(
  snapshot = makeSnapshot(),
  props: Partial<React.ComponentProps<typeof SubagentCard>> = {}
) {
  return render(
    <SubagentCard stream={stream} snapshot={snapshot} taskToolCall={taskToolCall} {...props} />
  );
}

// The card body stays mounted while collapsed (0fr grid row + inert), so
// presence/absence of the disclosure is asserted via aria-expanded and inert,
// never via text (dis)appearance.
const header = () => screen.getByRole("button", { name: /researcher/ });

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

  it("starts expanded while running and collapses/re-expands via the header", async () => {
    const user = userEvent.setup();
    const { container } = renderCard(makeSnapshot(), { isLoading: true });

    expect(header()).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("[inert]")).toBeNull();

    await user.click(header());
    expect(header()).toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector("[inert]")).not.toBeNull();

    await user.click(header());
    expect(header()).toHaveAttribute("aria-expanded", "true");
  });

  it("mounts collapsed when the subagent is already terminal (history reload)", () => {
    const { container } = renderCard(
      makeSnapshot({ status: "complete", completedAt: new Date("2026-07-01T10:05:00Z") }),
      { isLoading: false }
    );

    expect(header()).toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector("[inert]")).not.toBeNull();
    // The body is inert, not unmounted — its content stays queryable.
    expect(screen.getByText("Input")).toBeInTheDocument();
  });

  it("auto-collapses the moment the subagent completes mid-run", () => {
    const { rerender } = renderCard(makeSnapshot(), { isLoading: true });
    expect(header()).toHaveAttribute("aria-expanded", "true");

    rerender(
      <SubagentCard
        stream={stream}
        snapshot={makeSnapshot({
          status: "complete",
          completedAt: new Date("2026-07-01T10:05:00Z"),
        })}
        taskToolCall={taskToolCall}
        isLoading
      />
    );
    expect(header()).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the tool-count pill and duration once complete", () => {
    vi.mocked(useToolCalls).mockReturnValue([
      assembled(),
      assembled({ id: "nested-2", callId: "nested-2", name: "read_file" }),
    ]);
    renderCard(
      makeSnapshot({ status: "complete", completedAt: new Date("2026-07-01T10:05:07Z") }),
      { isLoading: false }
    );

    expect(screen.getByText("2 tools")).toBeInTheDocument();
    expect(screen.getByText("5m 07s")).toBeInTheDocument();
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

  it("labels calls issued together by one subagent step as parallel", () => {
    vi.mocked(useToolCalls).mockReturnValue([
      assembled(),
      assembled({ id: "nested-2", callId: "nested-2", name: "read_file" }),
      assembled({ id: "nested-3", callId: "nested-3", name: "edit_file" }),
    ]);
    vi.mocked(useMessages).mockReturnValue([
      new AIMessage({
        content: "",
        tool_calls: [
          { id: "nested-1", name: "internet_search", args: {}, type: "tool_call" },
          { id: "nested-2", name: "read_file", args: {}, type: "tool_call" },
        ],
      }),
      new AIMessage({
        content: "",
        tool_calls: [{ id: "nested-3", name: "edit_file", args: {}, type: "tool_call" }],
      }),
    ]);
    renderCard();

    expect(screen.getByText("2 in parallel")).toBeInTheDocument();
    expect(screen.getAllByText(/in parallel/)).toHaveLength(1);
  });
});

describe("SubagentCard nested HITL approvals", () => {
  const approval: PendingApproval = {
    key: "int1:0",
    interruptId: "int1",
    namespace: ["task:task-1"],
    index: 0,
    total: 1,
    actionRequest: {
      name: "execute",
      args: { command: "id" },
      description: "Review it before it executes.",
    },
  };

  function approvalsBundle(overrides: Partial<Record<string, unknown>> = {}) {
    return {
      pendingApprovals: [approval],
      approvalByToolCallId: new Map(),
      unassignedApprovals: [approval],
      interruptedToolCallIds: new Set<string>(),
      queuedDecisions: new Map(),
      decide: vi.fn(),
      claimedKeySet: new Set<string>(),
      registerSubagentClaims: vi.fn(),
      ...overrides,
    } as unknown as ApprovalsBundle;
  }

  // HITL fires in after_model, BEFORE the tool starts: the gated call exists
  // only on the messages channel (still pending), never on the tools channel.
  const pendingExecuteMessage = () =>
    new AIMessage({
      content: "",
      tool_calls: [{ id: "exec-1", name: "execute", args: { command: "id" }, type: "tool_call" }],
    });

  it("matches an unassigned approval to the pending nested call and renders the card", async () => {
    const user = userEvent.setup();
    vi.mocked(useMessages).mockReturnValue([pendingExecuteMessage()]);
    const approvals = approvalsBundle();
    // The run pauses on interrupt, so the snapshot reads terminal ("error").
    const { container } = renderCard(makeSnapshot({ status: "error" }), {
      isLoading: false,
      approvals,
    });

    // Review badge on the header (replacing "Failed") AND the status pill on
    // the nested interrupted box; pinned open.
    expect(screen.getAllByText("Needs review")).toHaveLength(2);
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
    expect(header()).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("[inert]")).toBeNull();

    // The claim is reported upward so the fallback block won't duplicate it.
    expect(approvals.registerSubagentClaims).toHaveBeenCalledWith("task-1", ["int1:0"]);

    expect(screen.getByText("Approval Required")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(approvals.decide).toHaveBeenCalledWith(approval, { type: "approve" });
  });

  it("stops spinning messages-only pending calls once the snapshot is terminal", () => {
    // A HITL-gated call never gets tools-channel events, and a resumed run's
    // events don't reach the mounted projection — without coercion its
    // spinner would run forever after the task completes.
    vi.mocked(useMessages).mockReturnValue([pendingExecuteMessage()]);
    const { container } = renderCard(
      makeSnapshot({ status: "complete", completedAt: new Date("2026-07-01T10:05:00Z") }),
      { isLoading: false }
    );

    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.getByText("execute")).toBeInTheDocument();
  });

  it("keeps the pending state (no coercion) while an approval is matched to the call", () => {
    vi.mocked(useMessages).mockReturnValue([pendingExecuteMessage()]);
    const approvals = approvalsBundle();
    renderCard(makeSnapshot({ status: "error" }), { isLoading: false, approvals });

    // Coercion must not swallow the interrupted rendering path.
    expect(screen.getByText("Approval Required")).toBeInTheDocument();
  });

  it("claims nothing when no nested call matches the approval", () => {
    vi.mocked(useMessages).mockReturnValue([
      new AIMessage({
        content: "",
        tool_calls: [
          { id: "s-1", name: "internet_search", args: { query: "x" }, type: "tool_call" },
        ],
      }),
    ]);
    const approvals = approvalsBundle();
    renderCard(makeSnapshot({ status: "error" }), { isLoading: false, approvals });

    expect(approvals.registerSubagentClaims).toHaveBeenCalledWith("task-1", []);
    expect(screen.queryByText("Approval Required")).not.toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});

describe("QueuedSubagentCard", () => {
  it("renders a header-only card with the Queued badge and no toggle", () => {
    render(<QueuedSubagentCard name="researcher" />);

    expect(screen.getByText("researcher")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it.each([
    ["hiring-recon", "lucide-radar"],
    ["resume-tailor", "lucide-scissors"],
    ["interview-coach", "lucide-messages-square"],
    ["some-new-agent", "lucide-bot"],
  ])("shows the %s identity icon (%s)", (name, iconClass) => {
    const { container } = render(<QueuedSubagentCard name={name} />);

    expect(container.querySelector(`.${iconClass}`)).not.toBeNull();
  });
});
