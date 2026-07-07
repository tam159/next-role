import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToolCallGroup } from "@/app/components/ToolCallGroup";
import type { ActionRequest, ToolCall } from "@/app/types/types";

vi.mock("@/app/components/ToolCallBox", () => ({
  ToolCallBox: ({
    toolCall,
    actionRequest,
  }: {
    toolCall: ToolCall;
    actionRequest?: ActionRequest;
  }) => (
    <div
      data-testid={`tool-call-box-${toolCall.id}`}
      data-action-request={actionRequest?.name ?? ""}
    />
  ),
}));

const toolCall = (overrides: Partial<ToolCall> = {}): ToolCall => ({
  id: "a1",
  name: "read_file",
  args: {},
  status: "completed",
  ...overrides,
});

// Two sequential batches, second still running / both done.
const runningBatches = [
  [toolCall()],
  [toolCall({ id: "b2", name: "edit_file", status: "pending" })],
];
const finishedBatches = [[toolCall()], [toolCall({ id: "b2", name: "edit_file" })]];

// The summary row is the only button the group itself renders (boxes are mocked).
const groupButton = () => screen.getByRole("button", { name: /tool calls/ });

describe("ToolCallGroup", () => {
  it("renders a single tool call as a bare box without a summary row", () => {
    render(<ToolCallGroup batches={[[toolCall()]]} />);

    expect(screen.getByTestId("tool-call-box-a1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /tool calls/ })).not.toBeInTheDocument();
  });

  it("is expanded with a live count and spinner while calls are running", () => {
    const { container } = render(<ToolCallGroup batches={runningBatches} isLoading />);

    expect(groupButton()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("2 tool calls")).toBeInTheDocument();
    expect(container.querySelector("[inert]")).toBeNull();
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("auto-collapses once every call is terminal", () => {
    const { container, rerender } = render(<ToolCallGroup batches={runningBatches} isLoading />);

    rerender(<ToolCallGroup batches={finishedBatches} isLoading={false} />);

    expect(groupButton()).toHaveAttribute("aria-expanded", "false");
    // Body stays mounted but inert — boxes remain queryable.
    expect(container.querySelector("[inert]")).not.toBeNull();
    expect(screen.getByTestId("tool-call-box-a1")).toBeInTheDocument();
  });

  it("mounts collapsed when already terminal (history reload)", () => {
    render(<ToolCallGroup batches={finishedBatches} isLoading={false} />);

    expect(groupButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("stays open between batches while the run's tip is still growing", () => {
    const { rerender } = render(<ToolCallGroup batches={[[toolCall()]]} isLoading isOpenEnded />);

    // All calls terminal, but the model may still append another batch.
    expect(screen.queryByRole("button", { name: /tool calls/ })).not.toBeInTheDocument();

    // A second batch arrives — now a real group, still open.
    rerender(<ToolCallGroup batches={finishedBatches} isLoading isOpenEnded />);
    expect(groupButton()).toHaveAttribute("aria-expanded", "true");

    // Prose starts streaming after the run: no longer the tip → collapses.
    rerender(<ToolCallGroup batches={finishedBatches} isLoading isOpenEnded={false} />);
    expect(groupButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps a user-expanded group open through later transitions", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ToolCallGroup batches={finishedBatches} isLoading={false} />);

    await user.click(groupButton());
    expect(groupButton()).toHaveAttribute("aria-expanded", "true");

    rerender(<ToolCallGroup batches={runningBatches} isLoading />);
    rerender(<ToolCallGroup batches={finishedBatches} isLoading={false} />);
    expect(groupButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps a user-collapsed group closed while still running", async () => {
    const user = userEvent.setup();
    render(<ToolCallGroup batches={runningBatches} isLoading />);

    await user.click(groupButton());
    expect(groupButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("pins the group open while a HITL interrupt is pending", async () => {
    const user = userEvent.setup();
    render(
      <ToolCallGroup
        batches={[
          [toolCall()],
          [toolCall({ id: "b2", name: "write_file", status: "interrupted" })],
        ]}
        isLoading={false}
      />
    );

    expect(groupButton()).toHaveAttribute("aria-expanded", "true");
    // The toggle is inert while pinned — collapsing would hide the approval UI.
    await user.click(groupButton());
    expect(groupButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("routes the approval request only to the interrupted call, not same-name completed ones", () => {
    const actionRequest: ActionRequest = { name: "write_file", args: {} };
    render(
      <ToolCallGroup
        batches={[
          [toolCall({ id: "a1", name: "write_file" })],
          [toolCall({ id: "b2", name: "write_file", status: "interrupted" })],
        ]}
        actionRequestsMap={new Map([["write_file", actionRequest]])}
      />
    );

    expect(screen.getByTestId("tool-call-box-b2")).toHaveAttribute(
      "data-action-request",
      "write_file"
    );
    expect(screen.getByTestId("tool-call-box-a1")).toHaveAttribute("data-action-request", "");
  });

  it("labels simultaneous batches with 'N in parallel' and leaves single steps unlabeled", () => {
    render(
      <ToolCallGroup
        batches={[
          [toolCall(), toolCall({ id: "b2", name: "edit_file" })],
          [toolCall({ id: "c3", name: "execute" })],
        ]}
        isLoading={false}
      />
    );

    expect(screen.getByText("2 in parallel")).toBeInTheDocument();
    // Exactly one label: the singleton second batch gets none.
    expect(screen.getAllByText(/in parallel/)).toHaveLength(1);
  });

  it("shows a failed pill and deduped name summary with overflow", () => {
    render(
      <ToolCallGroup
        batches={[
          [toolCall(), toolCall({ id: "b2" })],
          [toolCall({ id: "c3", name: "edit_file" })],
          [toolCall({ id: "d4", name: "execute", status: "error" })],
          [toolCall({ id: "e5", name: "internet_search" })],
        ]}
        isLoading={false}
      />
    );

    expect(screen.getByText("5 tool calls")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    // Four distinct names → first three shown, one overflows.
    expect(screen.getByText(/read_file · edit_file · execute\s\+1/)).toBeInTheDocument();
  });
});
