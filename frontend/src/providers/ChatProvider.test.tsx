import { render, renderHook } from "@testing-library/react";
import type { Assistant } from "@langchain/langgraph-sdk";
import { useChat } from "@/app/hooks/useChat";
import { ChatProvider, useChatContext } from "@/providers/ChatProvider";

vi.mock("@/app/hooks/useChat", () => ({ useChat: vi.fn() }));

const useChatMock = vi.mocked(useChat);

const assistant = {
  assistant_id: "asst-1",
  graph_id: "career_agent",
} as unknown as Assistant;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ChatProvider", () => {
  it("exposes the useChat return value through useChatContext", () => {
    const sentinel = { sendMessage: vi.fn(), files: {} } as unknown as ReturnType<typeof useChat>;
    useChatMock.mockReturnValue(sentinel);

    let captured: unknown;
    function Probe() {
      captured = useChatContext();
      return null;
    }

    render(
      <ChatProvider activeAssistant={assistant}>
        <Probe />
      </ChatProvider>
    );

    expect(captured).toBe(sentinel);
  });

  it("forwards its props to useChat", () => {
    useChatMock.mockReturnValue({} as ReturnType<typeof useChat>);
    const onHistoryRevalidate = vi.fn();

    render(
      <ChatProvider activeAssistant={assistant} onHistoryRevalidate={onHistoryRevalidate}>
        <span>child</span>
      </ChatProvider>
    );

    expect(useChatMock).toHaveBeenCalledWith({ activeAssistant: assistant, onHistoryRevalidate });
  });
});

describe("useChatContext", () => {
  it("throws its exact error when used outside a ChatProvider", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useChatContext())).toThrow(
      "useChatContext must be used within a ChatProvider"
    );
    errSpy.mockRestore();
  });
});
