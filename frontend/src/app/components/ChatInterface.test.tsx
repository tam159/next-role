import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { AIMessage, HumanMessage, type BaseMessage } from "@langchain/core/messages";
import type { Assistant } from "@langchain/langgraph-sdk";
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
  ChatMessage: ({ message }: { message: BaseMessage }) => (
    <div data-testid="chat-message">{message.id}</div>
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
