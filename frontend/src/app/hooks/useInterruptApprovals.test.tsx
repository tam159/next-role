import { act, renderHook } from "@testing-library/react";
import { AIMessage, ToolMessage, type BaseMessage } from "@langchain/core/messages";
import { approvalQueueNote, useInterruptApprovals } from "@/app/hooks/useInterruptApprovals";
import type { ApprovalDecision } from "@/app/types/types";

const executeAI = (id: string, calls: Array<{ id: string; command: string }>): AIMessage =>
  new AIMessage({
    id,
    content: "",
    tool_calls: calls.map((c) => ({
      id: c.id,
      name: "execute",
      args: { command: c.command },
      type: "tool_call",
    })),
  });

const taskAI = (id: string, taskId: string): AIMessage =>
  new AIMessage({
    id,
    content: "",
    tool_calls: [
      { id: taskId, name: "task", args: { subagent_type: "general-purpose" }, type: "tool_call" },
    ],
  });

const hitlValue = (commands: string[]) => ({
  action_requests: commands.map((command) => ({
    name: "execute",
    args: { command },
    description: "Review it before it executes.",
  })),
  review_configs: commands.map((_, index) => ({
    action_name: "execute",
    allowed_decisions: index === 0 ? ["approve", "edit", "reject"] : ["approve", "reject"],
  })),
});

interface HookInput {
  interrupts: { id: string; value: unknown }[];
  threadInterrupts?: { interruptId: string; payload: unknown; namespace: string[] }[];
  isLoading?: boolean;
  messages: BaseMessage[];
}

function setup(initial: HookInput) {
  const resumeInterrupt = vi.fn();
  const view = renderHook(
    (input: HookInput) =>
      useInterruptApprovals({
        interrupts: input.interrupts,
        getThreadInterrupts: () => input.threadInterrupts ?? [],
        isLoading: input.isLoading ?? false,
        messages: input.messages,
        resumeInterrupt,
      }),
    { initialProps: initial }
  );
  return { ...view, resumeInterrupt };
}

describe("useInterruptApprovals", () => {
  it("parses snake_case payloads and pairs review configs by index", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id", "uname -a"]) }],
      messages: [
        executeAI("m1", [
          { id: "t1", command: "id" },
          { id: "t2", command: "uname -a" },
        ]),
      ],
    });

    const approvals = result.current.pendingApprovals;
    expect(approvals).toHaveLength(2);
    expect(approvals[0].reviewConfig?.allowed_decisions).toEqual(["approve", "edit", "reject"]);
    // Index-aligned, NOT name-keyed: the same-name second entry differs.
    expect(approvals[1].reviewConfig?.allowed_decisions).toEqual(["approve", "reject"]);
    expect(approvals[1].key).toBe("int1:1");
    expect(approvals[1].total).toBe(2);
  });

  it("assigns approvals to tool calls by args, keeping same-name parallel calls distinct", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["uname -a", "id"]) }],
      // Reverse order vs the action_requests — args equality must win.
      messages: [
        executeAI("m1", [
          { id: "t1", command: "id" },
          { id: "t2", command: "uname -a" },
        ]),
      ],
    });

    expect(result.current.approvalByToolCallId.get("t2")?.actionRequest.args).toEqual({
      command: "uname -a",
    });
    expect(result.current.approvalByToolCallId.get("t1")?.actionRequest.args).toEqual({
      command: "id",
    });
    expect(result.current.interruptedToolCallIds).toEqual(new Set(["t1", "t2"]));
    expect(result.current.unassignedApprovals).toHaveLength(0);
  });

  it("leaves auto-approved (when-skipped) calls out of the interrupted set", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [
        executeAI("m1", [
          { id: "t1", command: "ls" },
          { id: "t2", command: "id" },
        ]),
      ],
    });

    expect(result.current.interruptedToolCallIds).toEqual(new Set(["t2"]));
  });

  it("never assigns to calls a ToolMessage already answered", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [
        executeAI("m1", [
          { id: "t1", command: "id" },
          { id: "t2", command: "id" },
        ]),
        new ToolMessage({ content: "uid=0", tool_call_id: "t1" }),
      ],
    });

    expect(result.current.interruptedToolCallIds).toEqual(new Set(["t2"]));
  });

  it("buckets subagent approvals as unassigned when the root call is a task", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [taskAI("m1", "task1")],
    });

    expect(result.current.approvalByToolCallId.size).toBe(0);
    expect(result.current.unassignedApprovals).toHaveLength(1);
    expect(result.current.unassignedApprovals[0].interruptId).toBe("int1");
  });

  it("accumulates decisions and resumes once with the ordered batch", () => {
    const { result, resumeInterrupt } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id", "uname -a"]) }],
      messages: [
        executeAI("m1", [
          { id: "t1", command: "id" },
          { id: "t2", command: "uname -a" },
        ]),
      ],
    });
    const [first, second] = result.current.pendingApprovals;

    const reject: ApprovalDecision = { type: "reject", message: "no" };
    act(() => result.current.decide(second, reject));
    expect(resumeInterrupt).not.toHaveBeenCalled();
    expect(result.current.queuedDecisions.get("int1:1")).toEqual(reject);
    expect(approvalQueueNote(second, result.current.queuedDecisions)).toBe(
      "Waiting on 1 more decision"
    );

    act(() => result.current.decide(first, { type: "approve" }));
    expect(resumeInterrupt).toHaveBeenCalledTimes(1);
    // Ordered by action_request index regardless of decision order.
    expect(resumeInterrupt).toHaveBeenCalledWith(
      { decisions: [{ type: "approve" }, reject] },
      { interruptId: "int1", namespace: undefined }
    );
  });

  it("never double-submits an interrupt, even on repeat decisions", () => {
    const { result, resumeInterrupt } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [executeAI("m1", [{ id: "t1", command: "id" }])],
    });
    const approval = result.current.pendingApprovals[0];

    act(() => result.current.decide(approval, { type: "approve" }));
    act(() => result.current.decide(approval, { type: "approve" }));

    expect(resumeInterrupt).toHaveBeenCalledTimes(1);
  });

  it("hides an interrupt locally once it has been responded to", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [executeAI("m1", [{ id: "t1", command: "id" }])],
    });

    act(() => result.current.decide(result.current.pendingApprovals[0], { type: "approve" }));

    expect(result.current.pendingApprovals).toHaveLength(0);
    expect(result.current.interruptedToolCallIds.size).toBe(0);
  });

  it("prunes queued decisions when their interrupt disappears", () => {
    const messages = [
      executeAI("m1", [
        { id: "t1", command: "id" },
        { id: "t2", command: "pwd" },
      ]),
    ];
    const { result, rerender } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id", "pwd"]) }],
      messages,
    });

    act(() => result.current.decide(result.current.pendingApprovals[0], { type: "approve" }));
    expect(result.current.queuedDecisions.size).toBe(1);

    // New run started — the interrupt is gone (e.g. user sent a new message).
    rerender({ interrupts: [], messages });
    expect(result.current.queuedDecisions.size).toBe(0);
  });

  it("admits live ThreadStream interrupts only while the thread is paused on open calls", () => {
    const pausedInput: HookInput = {
      interrupts: [],
      threadInterrupts: [
        { interruptId: "int9", payload: hitlValue(["id"]), namespace: ["task:task1"] },
      ],
      messages: [taskAI("m1", "task1")],
    };
    const { result, rerender } = setup(pausedInput);

    expect(result.current.unassignedApprovals).toHaveLength(1);
    expect(result.current.unassignedApprovals[0].namespace).toEqual(["task:task1"]);

    // Streaming run → replayed entries must not surface.
    rerender({ ...pausedInput, isLoading: true });
    expect(result.current.pendingApprovals).toHaveLength(0);

    // No open calls (everything answered) → nothing to review either.
    rerender({
      ...pausedInput,
      messages: [
        taskAI("m1", "task1"),
        new ToolMessage({ content: "done", tool_call_id: "task1" }),
      ],
    });
    expect(result.current.pendingApprovals).toHaveLength(0);
  });

  it("ignores non-HITL interrupt payloads", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: { question: "what color?" } }],
      messages: [executeAI("m1", [{ id: "t1", command: "id" }])],
    });

    expect(result.current.pendingApprovals).toHaveLength(0);
  });

  it("tracks subagent claims with stable updates", () => {
    const { result } = setup({
      interrupts: [{ id: "int1", value: hitlValue(["id"]) }],
      messages: [taskAI("m1", "task1")],
    });

    act(() => result.current.registerSubagentClaims("sub1", ["int1:0"]));
    expect(result.current.claimedKeySet).toEqual(new Set(["int1:0"]));

    // Same keys again — no change (compare-before-set guards effect loops).
    const before = result.current.claimedKeySet;
    act(() => result.current.registerSubagentClaims("sub1", ["int1:0"]));
    expect(result.current.claimedKeySet).toBe(before);

    act(() => result.current.registerSubagentClaims("sub1", []));
    expect(result.current.claimedKeySet.size).toBe(0);
  });
});
