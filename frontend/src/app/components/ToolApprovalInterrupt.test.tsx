import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToolApprovalInterrupt } from "@/app/components/ToolApprovalInterrupt";
import type { ActionRequest, ReviewConfig } from "@/app/types/types";

const actionRequest: ActionRequest = {
  name: "write_file",
  args: { path: "/tmp/draft.md", content: "hello" },
  description: "The agent wants to write the draft file.",
};

function renderInterrupt(
  props: {
    onResume?: (value: any) => void;
    reviewConfig?: ReviewConfig;
    isLoading?: boolean;
    request?: ActionRequest;
  } = {}
) {
  const onResume = props.onResume ?? vi.fn();
  render(
    <ToolApprovalInterrupt
      actionRequest={props.request ?? actionRequest}
      reviewConfig={props.reviewConfig}
      onResume={onResume}
      isLoading={props.isLoading}
    />
  );
  return { onResume };
}

describe("ToolApprovalInterrupt", () => {
  it("renders the header, description, tool name and current arguments", () => {
    renderInterrupt();

    expect(screen.getByText("Approval Required")).toBeInTheDocument();
    expect(screen.getByText("The agent wants to write the draft file.")).toBeInTheDocument();
    expect(screen.getByText("write_file")).toBeInTheDocument();
    expect(screen.getByText(/"path": "\/tmp\/draft\.md"/)).toBeInTheDocument();
  });

  it("emits an approve decision when Approve is clicked", async () => {
    const user = userEvent.setup();
    const { onResume } = renderInterrupt();

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(onResume).toHaveBeenCalledTimes(1);
    expect(onResume).toHaveBeenCalledWith({ decisions: [{ type: "approve" }] });
  });

  it("reveals the rejection message input first, then confirms with the trimmed message", async () => {
    const user = userEvent.setup();
    const { onResume } = renderInterrupt();

    await user.click(screen.getByRole("button", { name: "Reject" }));
    // First click only reveals the input; nothing is resumed yet.
    expect(onResume).not.toHaveBeenCalled();
    expect(screen.getByText("Rejection Message (optional)")).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText("Explain why you're rejecting this action...");
    await user.type(textarea, "  too risky  ");
    await user.click(screen.getByRole("button", { name: "Confirm Reject" }));

    expect(onResume).toHaveBeenCalledWith({
      decisions: [{ type: "reject", message: "too risky" }],
    });
  });

  it("cancels the rejection flow and returns to the decision buttons", async () => {
    const user = userEvent.setup();
    const { onResume } = renderInterrupt();

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onResume).not.toHaveBeenCalled();
    expect(screen.queryByText("Rejection Message (optional)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("edits arguments and submits an edit decision with the edited_action", async () => {
    const user = userEvent.setup();
    const { onResume } = renderInterrupt();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByText("Edit Arguments")).toBeInTheDocument();

    const [pathField, contentField] = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(pathField).toHaveValue("/tmp/draft.md");
    expect(contentField).toHaveValue("hello");

    await user.clear(pathField);
    await user.type(pathField, "notes/final.md");
    await user.click(screen.getByRole("button", { name: "Save & Approve" }));

    expect(onResume).toHaveBeenCalledWith({
      decisions: [
        {
          type: "edit",
          edited_action: {
            name: "write_file",
            args: { path: "notes/final.md", content: "hello" },
          },
        },
      ],
    });
  });

  it("parses JSON-looking edited values into objects", async () => {
    const user = userEvent.setup();
    const { onResume } = renderInterrupt();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const [, contentField] = screen.getAllByRole("textbox");

    await user.clear(contentField);
    await user.click(contentField);
    await user.paste('{"tone": "formal"}');
    await user.click(screen.getByRole("button", { name: "Save & Approve" }));

    expect(onResume).toHaveBeenCalledWith({
      decisions: [
        {
          type: "edit",
          edited_action: {
            name: "write_file",
            args: { path: "/tmp/draft.md", content: { tone: "formal" } },
          },
        },
      ],
    });
  });

  it("hides reject and edit when the review config only allows approve", () => {
    renderInterrupt({
      reviewConfig: { actionName: "write_file", allowedDecisions: ["approve"] },
    });

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("disables all controls while loading", () => {
    renderInterrupt({ isLoading: true });

    // The approve button swaps its label to "Approving..." while loading.
    expect(screen.getByRole("button", { name: "Approving..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  });
});
