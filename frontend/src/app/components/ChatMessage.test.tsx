import { render, screen } from "@testing-library/react";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";
import type { AnyStream, SubagentDiscoverySnapshot } from "@langchain/react";
import { ChatMessage } from "@/app/components/ChatMessage";
import type { ActionRequest, ToolCall } from "@/app/types/types";

vi.mock("@/app/components/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

vi.mock("@/app/components/ToolCallBox", () => ({
  ToolCallBox: ({
    toolCall,
    actionRequest,
    onResume,
  }: {
    toolCall: ToolCall;
    actionRequest?: ActionRequest;
    onResume?: (value: unknown) => void;
  }) => (
    <div
      data-testid={`tool-call-box-${toolCall.id}`}
      data-name={toolCall.name}
      data-action-request={actionRequest?.name ?? ""}
      data-has-resume={onResume ? "yes" : "no"}
    />
  ),
}));

vi.mock("@/app/components/SubagentCard", () => ({
  SubagentCard: ({
    snapshot,
    taskToolCall,
  }: {
    snapshot: SubagentDiscoverySnapshot;
    taskToolCall: ToolCall;
  }) => <div data-testid={`subagent-card-${taskToolCall.id}`} data-snapshot-name={snapshot.name} />,
  QueuedSubagentCard: ({ name }: { name: string }) => (
    <div data-testid={`queued-subagent-card-${name}`} />
  ),
}));

function makeStream(subagents = new Map<string, SubagentDiscoverySnapshot>()): AnyStream {
  return { subagents } as unknown as AnyStream;
}

function renderMessage(
  message: BaseMessage,
  props: Partial<React.ComponentProps<typeof ChatMessage>> = {}
) {
  return render(<ChatMessage message={message} toolCalls={[]} stream={makeStream()} {...props} />);
}

const toolCall = (overrides: Partial<ToolCall> = {}): ToolCall => ({
  id: "call-1",
  name: "internet_search",
  args: { query: "acme" },
  status: "completed",
  ...overrides,
});

describe("ChatMessage", () => {
  it("renders a human message as a plain user bubble", () => {
    renderMessage(new HumanMessage("Please review my CV"));

    const text = screen.getByText("Please review my CV");
    expect(text.tagName).toBe("P");
    // User text is not routed through MarkdownContent and gets no avatar.
    expect(screen.queryByTestId("markdown")).not.toBeInTheDocument();
    expect(screen.queryByAltText("NextRole")).not.toBeInTheDocument();
  });

  it("renders AI text through MarkdownContent with the avatar", () => {
    renderMessage(new AIMessage("## Summary\nLooks solid overall."));

    expect(screen.getByTestId("markdown")).toHaveTextContent("Summary Looks solid overall.");
    expect(screen.getByAltText("NextRole")).toBeInTheDocument();
  });

  it("renders the merged tool run headed by this message as ToolCallBoxes", () => {
    renderMessage(new AIMessage(""), {
      toolBatches: [
        [toolCall({ id: "a1", name: "internet_search" })],
        [toolCall({ id: "b2", name: "read_file", args: { path: "/x" } })],
      ],
    });

    expect(screen.getByTestId("tool-call-box-a1")).toHaveAttribute("data-name", "internet_search");
    expect(screen.getByTestId("tool-call-box-b2")).toHaveAttribute("data-name", "read_file");
  });

  it("renders no tool boxes when this message's calls belong to an earlier head", () => {
    renderMessage(new AIMessage(""), {
      toolCalls: [toolCall({ id: "a1", name: "internet_search" })],
      toolBatches: null,
    });

    expect(screen.queryByTestId("tool-call-box-a1")).not.toBeInTheDocument();
  });

  it("routes the action request to the interrupted tool call box", () => {
    const actionRequest: ActionRequest = { name: "write_file", args: { path: "/tmp/a.md" } };
    const onResumeInterrupt = vi.fn();
    renderMessage(new AIMessage(""), {
      toolBatches: [
        [
          toolCall({ id: "a1", name: "write_file", status: "interrupted" }),
          toolCall({ id: "b2", name: "execute" }),
        ],
      ],
      actionRequestsMap: new Map([["write_file", actionRequest]]),
      onResumeInterrupt,
    });

    // actionRequestsMap is keyed by tool NAME and attaches only to the
    // interrupted call; every box receives onResume.
    const interrupted = screen.getByTestId("tool-call-box-a1");
    expect(interrupted).toHaveAttribute("data-action-request", "write_file");
    expect(interrupted).toHaveAttribute("data-has-resume", "yes");
    expect(screen.getByTestId("tool-call-box-b2")).toHaveAttribute("data-action-request", "");
  });

  it("renders a SubagentCard for a task call once its discovery snapshot exists", () => {
    const task = toolCall({
      id: "task-1",
      name: "task",
      args: { subagent_type: "researcher", description: "Dig into Acme" },
      status: "pending",
    });
    const snapshot = { id: "task-1", name: "researcher" } as SubagentDiscoverySnapshot;
    renderMessage(new AIMessage(""), {
      toolCalls: [task],
      stream: makeStream(new Map([["task-1", snapshot]])),
    });

    expect(screen.getByTestId("subagent-card-task-1")).toHaveAttribute(
      "data-snapshot-name",
      "researcher"
    );
    // Task calls never render as plain tool call boxes.
    expect(screen.queryByTestId("tool-call-box-task-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("queued-subagent-card-researcher")).not.toBeInTheDocument();
  });

  it("renders a queued subagent card for a task call before discovery lands", () => {
    const task = toolCall({
      id: "task-1",
      name: "task",
      args: { subagent_type: "researcher" },
      status: "pending",
    });
    renderMessage(new AIMessage(""), { toolCalls: [task] });

    expect(screen.getByTestId("queued-subagent-card-researcher")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-card-task-1")).not.toBeInTheDocument();
  });

  it("ignores task calls that have no subagent_type yet", () => {
    renderMessage(new AIMessage(""), {
      toolCalls: [toolCall({ id: "task-1", name: "task", args: {} })],
    });

    expect(screen.queryByTestId("subagent-card-task-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("queued-subagent-card-undefined")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tool-call-box-task-1")).not.toBeInTheDocument();
  });

  it("shows the thinking indicator while loading with no content or tool calls", () => {
    renderMessage(new AIMessage(""), { isLoading: true });

    expect(screen.getByText("Working through your request")).toBeInTheDocument();
  });
});
