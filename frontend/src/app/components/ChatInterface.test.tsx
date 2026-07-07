import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { AIMessage, HumanMessage, ToolMessage, type BaseMessage } from "@langchain/core/messages";
import type { Assistant } from "@langchain/langgraph-sdk";
import type { ToolCall } from "@/app/types/types";
import { ChatInterface } from "./ChatInterface";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// ChatInterface owns no composer state — `input`/`setInput` live on the chat
// context — so the mock hook keeps the input in real React state to make
// typing, suggestion fills, and post-send clearing observable.
const sendMessage = vi.fn();
const stopStream = vi.fn();
const resumeInterrupt = vi.fn();
const refreshFiles = vi.fn(async () => {});
const appendUploadNote = vi.fn();

const ctx: { messages: BaseMessage[]; isLoading: boolean; isThreadLoading: boolean } = {
  messages: [],
  isLoading: false,
  isThreadLoading: false,
};

function useMockChatContext() {
  const [input, setInput] = React.useState("");
  return {
    stream: null,
    messages: ctx.messages,
    isLoading: ctx.isLoading,
    isThreadLoading: ctx.isThreadLoading,
    interrupt: undefined,
    sendMessage,
    stopStream,
    resumeInterrupt,
    input,
    setInput,
    focusComposerNonce: 0,
    refreshFiles,
    appendUploadNote,
  };
}

vi.mock("@/providers/ChatProvider", () => ({
  useChatContext: () => useMockChatContext(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const uploadAgentFilesMock = vi.hoisted(() =>
  vi.fn(async () => ({ uploaded: [] as { path: string; size: number }[], errors: [] }))
);
vi.mock("@/app/lib/uploadFiles", () => ({
  CAREER_AGENT_UPLOAD_DIR: "backend/agents/career_agent/upload",
  uploadAgentFiles: uploadAgentFilesMock,
}));

vi.mock("@/app/components/ChatMessage", () => ({
  ChatMessage: ({
    message,
    toolBatches,
    isOpenEndedGroup,
  }: {
    message: BaseMessage;
    toolBatches?: ToolCall[][] | null;
    isOpenEndedGroup?: boolean;
  }) => (
    <div
      data-testid="chat-message"
      data-batches={
        toolBatches ? JSON.stringify(toolBatches.map((b) => b.map((tc) => tc.name))) : ""
      }
      data-open-ended={isOpenEndedGroup ? "yes" : "no"}
    >
      {message.id}
    </div>
  ),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const assistant = { assistant_id: "assistant-1", graph_id: "career_agent" } as unknown as Assistant;

function renderChat() {
  // use-stick-to-bottom runs for real in every render (ResizeObserver/scrollTo
  // polyfills come from vitest.setup.ts) — rendering without crashing is the
  // smoke assertion for it.
  return render(<ChatInterface assistant={assistant} />);
}

const composer = () => screen.getByPlaceholderText(/Message NextRole/);

beforeEach(() => {
  vi.clearAllMocks();
  ctx.messages = [];
  ctx.isLoading = false;
  ctx.isThreadLoading = false;
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChatInterface", () => {
  it("sends the trimmed composer text on Enter and clears the input", async () => {
    const user = userEvent.setup();
    renderChat();
    const textarea = composer();

    await user.type(textarea, "  hello world  ");
    await user.keyboard("{Enter}");

    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage).toHaveBeenCalledWith("hello world");
    expect(textarea).toHaveValue("");
  });

  it("inserts a newline on Shift+Enter instead of sending", async () => {
    const user = userEvent.setup();
    renderChat();
    const textarea = composer();

    await user.type(textarea, "line one");
    await user.keyboard("{Shift>}{Enter}{/Shift}line two");

    expect(sendMessage).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("line one\nline two");
  });

  it("disables Send and ignores Enter while the composer is empty or whitespace", async () => {
    const user = userEvent.setup();
    renderChat();
    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).toBeDisabled();

    const textarea = composer();
    await user.type(textarea, "   ");
    expect(sendButton).toBeDisabled();

    await user.keyboard("{Enter}");
    expect(sendMessage).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("   ");
  });

  it("shows the stop control while streaming and stops the stream on click", async () => {
    const user = userEvent.setup();
    ctx.isLoading = true;
    renderChat();

    expect(screen.getByText(/NextRole is working/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(stopStream).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("renders one ChatMessage per human/AI message, in order", () => {
    ctx.messages = [
      new HumanMessage({ id: "h1", content: "hi" }),
      new AIMessage({ id: "a1", content: "hello" }),
    ];
    renderChat();

    const stubs = screen.getAllByTestId("chat-message");
    expect(stubs).toHaveLength(2);
    expect(stubs[0]).toHaveTextContent("h1");
    expect(stubs[1]).toHaveTextContent("a1");
    expect(screen.queryByRole("heading", { name: /land your next role/i })).not.toBeInTheDocument();
  });

  it("shows the welcome empty state and fills the composer from a suggestion chip", async () => {
    const user = userEvent.setup();
    renderChat();

    expect(screen.getByRole("heading", { name: /land your next role/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /tailor my resume/i }));
    expect(composer()).toHaveValue("Tailor my resume to a specific job description.");
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("shows the thread-loading placeholder instead of messages or the hero", () => {
    ctx.isThreadLoading = true;
    renderChat();

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /land your next role/i })).not.toBeInTheDocument();
  });

  // COMPOSER_ATTACH_ENABLED is hardcoded to false in the component, so the
  // hidden file input never renders and the uploadAgentFiles/appendUploadNote
  // path is unreachable from the composer. Documented here as the flag-off
  // behavior; the upload flow itself lives in Workspace > Files.
  it("renders no attach control while the composer paperclip flag is off", () => {
    renderChat();

    expect(screen.queryByTitle("Attach a file")).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(uploadAgentFilesMock).not.toHaveBeenCalled();
    expect(appendUploadNote).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tool-call run grouping (computed here, rendered by ToolCallGroup)
// ---------------------------------------------------------------------------

const aiWithTools = (
  id: string,
  tools: Array<{ id: string; name: string }>,
  content = ""
): AIMessage =>
  new AIMessage({
    id,
    content,
    tool_calls: tools.map((t) => ({ id: t.id, name: t.name, args: {}, type: "tool_call" })),
  });

const toolResult = (callId: string): ToolMessage =>
  new ToolMessage({ content: "ok", tool_call_id: callId });

const batchesOf = (stub: HTMLElement) => stub.getAttribute("data-batches");

describe("ChatInterface tool-call run grouping", () => {
  it("merges consecutive AI tool messages into the head's batches", () => {
    ctx.messages = [
      aiWithTools("m1", [
        { id: "t1", name: "read_file" },
        { id: "t2", name: "read_file" },
      ]),
      toolResult("t1"),
      toolResult("t2"),
      aiWithTools("m2", [{ id: "t3", name: "edit_file" }]),
      toolResult("t3"),
      new AIMessage({ id: "m3", content: "done" }),
    ];
    renderChat();

    const [m1, m2, m3] = screen.getAllByTestId("chat-message");
    expect(batchesOf(m1)).toBe('[["read_file","read_file"],["edit_file"]]');
    expect(batchesOf(m2)).toBe("");
    expect(batchesOf(m3)).toBe("");
  });

  it("breaks a run on prose before its own tools and on a subagent spawn after them", () => {
    ctx.messages = [
      aiWithTools("m1", [{ id: "t1", name: "read_file" }]),
      toolResult("t1"),
      // Prose renders above this message's own tools → new head here.
      aiWithTools("m2", [{ id: "t2", name: "edit_file" }], "Let me update that."),
      toolResult("t2"),
      // Its regular call joins m2's run; the subagent card then ends it.
      new AIMessage({
        id: "m3",
        content: "",
        tool_calls: [
          { id: "t3", name: "list_files", args: {}, type: "tool_call" },
          { id: "task1", name: "task", args: { subagent_type: "researcher" }, type: "tool_call" },
        ],
      }),
      toolResult("t3"),
      aiWithTools("m4", [{ id: "t4", name: "execute" }]),
      toolResult("t4"),
    ];
    renderChat();

    const [m1, m2, m3, m4] = screen.getAllByTestId("chat-message");
    expect(batchesOf(m1)).toBe('[["read_file"]]');
    expect(batchesOf(m2)).toBe('[["edit_file"],["list_files"]]');
    expect(batchesOf(m3)).toBe("");
    expect(batchesOf(m4)).toBe('[["execute"]]');
  });

  it("flags only a transcript that ends inside a run as open-ended", () => {
    ctx.messages = [aiWithTools("m1", [{ id: "t1", name: "read_file" }])];
    ctx.isLoading = true;
    renderChat();
    expect(screen.getByTestId("chat-message")).toHaveAttribute("data-open-ended", "yes");
  });

  it("clears the open-ended flag once prose follows the run", () => {
    ctx.messages = [
      aiWithTools("m1", [{ id: "t1", name: "read_file" }]),
      toolResult("t1"),
      new AIMessage({ id: "m2", content: "All set." }),
    ];
    ctx.isLoading = true;
    renderChat();

    const [m1, m2] = screen.getAllByTestId("chat-message");
    expect(m1).toHaveAttribute("data-open-ended", "no");
    expect(m2).toHaveAttribute("data-open-ended", "no");
  });
});
