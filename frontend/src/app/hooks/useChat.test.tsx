import { act, renderHook, waitFor } from "@testing-library/react";
import { HumanMessage } from "@langchain/core/messages";
import { useStream } from "@langchain/react";
import type { Assistant } from "@langchain/langgraph-sdk";
import { useQueryState } from "nuqs";
import { useClient } from "@/providers/ClientProvider";
import {
  fetchAgentFiles,
  getAgentFileSources,
  resolveStoreLocation,
  writeAgentFile,
  type AgentFile,
} from "@/app/lib/agentFiles";
import { deleteAgentFile } from "@/app/lib/uploadFiles";
import { getConfig, type StandaloneConfig } from "@/lib/config";
import type { AgentFileSources } from "@/app/config/agentFiles";
import { useChat } from "@/app/hooks/useChat";

vi.mock("@langchain/react", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useStream: vi.fn(),
}));
vi.mock("nuqs", () => ({ useQueryState: vi.fn() }));
vi.mock("@/providers/ClientProvider", () => ({ useClient: vi.fn() }));
vi.mock("@/app/lib/agentFiles", () => ({
  fetchAgentFiles: vi.fn(),
  getAgentFileSources: vi.fn(),
  resolveStoreLocation: vi.fn(),
  writeAgentFile: vi.fn(),
}));
vi.mock("@/app/lib/uploadFiles", () => ({ deleteAgentFile: vi.fn() }));
vi.mock("@/lib/config", () => ({ getConfig: vi.fn() }));

const useStreamMock = vi.mocked(useStream);
const useQueryStateMock = vi.mocked(useQueryState);
const useClientMock = vi.mocked(useClient);
const fetchAgentFilesMock = vi.mocked(fetchAgentFiles);
const getAgentFileSourcesMock = vi.mocked(getAgentFileSources);
const resolveStoreLocationMock = vi.mocked(resolveStoreLocation);
const writeAgentFileMock = vi.mocked(writeAgentFile);
const deleteAgentFileMock = vi.mocked(deleteAgentFile);
const getConfigMock = vi.mocked(getConfig);

const assistant = {
  assistant_id: "asst-123",
  graph_id: "career_agent",
  config: { configurable: { base_key: "base" }, tags: ["prod"] },
} as unknown as Assistant;

const careerSources: AgentFileSources = {
  store: { namespacePrefix: ["career_agent"], pathPrefixes: ["/processed/"] },
  disk: { root: "backend/agents/career_agent", includeDirs: ["upload", "tailored_resume"] },
};

const diskFile: AgentFile = {
  path: "/upload/cv.md",
  content: "old disk content",
  encoding: "utf-8",
  source: "disk",
  sourceKey: "/backend/agents/career_agent/upload/cv.md",
};

const stateFile: AgentFile = {
  path: "/notes.md",
  content: "keep",
  encoding: "utf-8",
  source: "state",
  sourceKey: "/notes.md",
};

function makeStream(overrides: Record<string, unknown> = {}) {
  return {
    submit: vi.fn().mockResolvedValue(undefined),
    respond: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    values: { files: {}, todos: [] } as Record<string, unknown>,
    messages: [] as unknown[],
    isLoading: false,
    isThreadLoading: false,
    interrupt: undefined as unknown,
    subagents: new Map(),
    ...overrides,
  };
}

async function setup(
  opts: {
    stream?: ReturnType<typeof makeStream>;
    threadId?: string | null;
    agentFiles?: AgentFile[];
    sources?: AgentFileSources;
    config?: StandaloneConfig | null;
    activeAssistant?: Assistant | null;
  } = {}
) {
  const {
    stream = makeStream(),
    threadId = "thread-1",
    agentFiles = [],
    sources = undefined,
    config = null,
    activeAssistant = assistant,
  } = opts;

  const setThreadId = vi.fn();
  const client = { threads: { updateState: vi.fn().mockResolvedValue(undefined) } };

  useQueryStateMock.mockReturnValue([threadId, setThreadId] as never);
  useClientMock.mockReturnValue(client as never);
  useStreamMock.mockReturnValue(stream as never);
  getConfigMock.mockReturnValue(config);
  getAgentFileSourcesMock.mockReturnValue(sources);
  resolveStoreLocationMock.mockReturnValue(null);
  fetchAgentFilesMock.mockResolvedValue(agentFiles);
  writeAgentFileMock.mockResolvedValue(undefined);
  deleteAgentFileMock.mockResolvedValue(undefined);

  const onHistoryRevalidate = vi.fn();
  const utils = renderHook(() => useChat({ activeAssistant, onHistoryRevalidate }));
  // Flush the mount-time refreshFiles() chain so extendedFiles settles inside act.
  await act(async () => {});
  return { ...utils, stream, client, setThreadId, onHistoryRevalidate };
}

const lastStreamArgs = () => useStreamMock.mock.calls.at(-1)?.[0] as any;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useStream wiring", () => {
  it("passes assistant id, client, thread id, and history callbacks", async () => {
    const { client, setThreadId, onHistoryRevalidate } = await setup();

    const args = lastStreamArgs();
    expect(args.assistantId).toBe("asst-123");
    expect(args.client).toBe(client);
    expect(args.threadId).toBe("thread-1");
    expect(args.onThreadId).toBe(setThreadId);

    args.onCreated();
    args.onCompleted();
    expect(onHistoryRevalidate).toHaveBeenCalledTimes(2);
  });

  it("holds the thread back until the assistant resolves", async () => {
    await setup({ activeAssistant: null, threadId: "t-existing" });

    const args = lastStreamArgs();
    expect(args.assistantId).toBe("");
    expect(args.threadId).toBeNull();
  });
});

describe("sendMessage", () => {
  it("submits a human message with the merged assistant + model-override config", async () => {
    const { result, stream, onHistoryRevalidate } = await setup({
      config: {
        deploymentUrl: "http://x",
        assistantId: "a",
        mainAgentModel: "claude-big",
        subagentModel: "claude-small",
      },
    });

    act(() => {
      result.current.sendMessage("hi");
    });

    expect(stream.submit).toHaveBeenCalledTimes(1);
    const [payload, options] = stream.submit.mock.calls[0];
    expect(payload.messages).toHaveLength(1);
    expect(payload.messages[0]).toBeInstanceOf(HumanMessage);
    expect(payload.messages[0].content).toBe("hi");
    expect(options).toEqual({
      config: {
        tags: ["prod"],
        recursion_limit: 100,
        configurable: {
          base_key: "base",
          main_agent_model: "claude-big",
          subagent_model: "claude-small",
        },
      },
    });
    expect(onHistoryRevalidate).toHaveBeenCalledTimes(1);
  });

  it("omits model overrides when no user config is stored", async () => {
    const { result, stream } = await setup({ config: null });

    act(() => {
      result.current.sendMessage("hello");
    });

    const [, options] = stream.submit.mock.calls[0];
    expect(options.config).toEqual({
      tags: ["prod"],
      recursion_limit: 100,
      configurable: { base_key: "base" },
    });
  });
});

describe("resumeInterrupt / stopStream", () => {
  it("responds to the interrupt with the value and merged config, then revalidates", async () => {
    const { result, stream, onHistoryRevalidate } = await setup();

    act(() => {
      result.current.resumeInterrupt({ decision: "approve" });
    });

    expect(stream.respond).toHaveBeenCalledTimes(1);
    expect(stream.respond).toHaveBeenCalledWith(
      { decision: "approve" },
      { config: { tags: ["prod"], configurable: { base_key: "base" } } }
    );
    expect(onHistoryRevalidate).toHaveBeenCalledTimes(1);
  });

  it("stopStream stops the stream", async () => {
    const { result, stream } = await setup();

    act(() => {
      result.current.stopStream();
    });

    expect(stream.stop).toHaveBeenCalledTimes(1);
  });
});

describe("stream state passthrough", () => {
  it("exposes todos, files, email, messages, flags, interrupt, and subagents", async () => {
    const todos = [{ id: "t1", content: "do it", status: "pending" }];
    const email = { id: "e1", subject: "offer" };
    const interrupt = { action_requests: [] };
    const messages = [{ id: "m1" }];
    const subagents = new Map([["call-1", { id: "sub-1" }]]);
    const stream = makeStream({
      values: { files: {}, todos, email },
      isLoading: true,
      isThreadLoading: true,
      interrupt,
      messages,
      subagents,
    });

    const { result } = await setup({ stream, agentFiles: [stateFile] });

    expect(result.current.todos).toBe(todos);
    expect(result.current.email).toBe(email);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isThreadLoading).toBe(true);
    expect(result.current.interrupt).toBe(interrupt);
    expect(result.current.messages).toBe(messages);
    expect(result.current.subagents).toBe(subagents);
    await waitFor(() => expect(result.current.files).toEqual({ "/notes.md": "keep" }));
  });

  it("defaults todos to an empty array when the stream has none", async () => {
    const { result } = await setup({ stream: makeStream({ values: {} }) });
    expect(result.current.todos).toEqual([]);
  });
});

describe("setFiles", () => {
  it("writes the full state map through threads.updateState when no external sources", async () => {
    const { result, client } = await setup({ sources: undefined });
    fetchAgentFilesMock.mockClear();

    await act(async () => {
      await result.current.setFiles({ "/a.md": "hello" });
    });

    expect(client.threads.updateState).toHaveBeenCalledTimes(1);
    expect(client.threads.updateState).toHaveBeenCalledWith("thread-1", {
      values: { files: { "/a.md": "hello" } },
    });
    expect(writeAgentFileMock).not.toHaveBeenCalled();
    expect(fetchAgentFilesMock).not.toHaveBeenCalled();
  });

  it("is a no-op without a threadId when no external sources", async () => {
    const { result, client } = await setup({ sources: undefined, threadId: null });

    await act(async () => {
      await result.current.setFiles({ "/a.md": "hello" });
    });

    expect(client.threads.updateState).not.toHaveBeenCalled();
  });

  it("routes a changed disk file to writeAgentFile with its existing sourceKey", async () => {
    const { result, client } = await setup({ sources: careerSources, agentFiles: [diskFile] });
    await waitFor(() => expect(result.current.files["/upload/cv.md"]).toBe("old disk content"));
    fetchAgentFilesMock.mockClear();

    await act(async () => {
      await result.current.setFiles({ "/upload/cv.md": "new disk content" });
    });

    expect(writeAgentFileMock).toHaveBeenCalledTimes(1);
    expect(writeAgentFileMock).toHaveBeenCalledWith({
      client,
      threadId: "thread-1",
      graphId: "career_agent",
      file: { ...diskFile, content: "new disk content" },
    });
    expect(client.threads.updateState).not.toHaveBeenCalled();
    // Refetches to pick up new modified_at.
    expect(fetchAgentFilesMock).toHaveBeenCalledTimes(1);
  });

  it("routes a new path matching a store pathPrefix to a synthesized store write", async () => {
    const { result, client } = await setup({ sources: careerSources });
    resolveStoreLocationMock.mockImplementation((_cfg, path) =>
      path.startsWith("/processed/")
        ? { namespace: ["career_agent", "processed"], key: path.slice("/processed".length) }
        : null
    );

    await act(async () => {
      await result.current.setFiles({ "/processed/report.md": "store body" });
    });

    expect(resolveStoreLocationMock).toHaveBeenCalledWith(
      careerSources.store,
      "/processed/report.md"
    );
    expect(writeAgentFileMock).toHaveBeenCalledTimes(1);
    expect(writeAgentFileMock).toHaveBeenCalledWith({
      client,
      threadId: "thread-1",
      graphId: "career_agent",
      file: {
        path: "/processed/report.md",
        content: "store body",
        encoding: "utf-8",
        source: "store",
        sourceKey: "/processed/report.md",
      },
    });
    expect(client.threads.updateState).not.toHaveBeenCalled();
  });

  it("routes a new path under a disk includeDir to a disk write with a synthesized sourceKey", async () => {
    const { result, client } = await setup({ sources: careerSources });

    await act(async () => {
      await result.current.setFiles({ "/upload/new.pdf": "pdf bytes" });
    });

    expect(writeAgentFileMock).toHaveBeenCalledTimes(1);
    expect(writeAgentFileMock).toHaveBeenCalledWith({
      client,
      threadId: "thread-1",
      graphId: "career_agent",
      file: {
        path: "/upload/new.pdf",
        content: "pdf bytes",
        encoding: "utf-8",
        source: "disk",
        sourceKey: "/backend/agents/career_agent/upload/new.pdf",
      },
    });
    expect(client.threads.updateState).not.toHaveBeenCalled();
  });

  it("falls back to a state update for unmatched new paths, carrying unchanged state files", async () => {
    const { result, client } = await setup({
      sources: careerSources,
      agentFiles: [stateFile, diskFile],
    });
    await waitFor(() => expect(Object.keys(result.current.files)).toHaveLength(2));

    await act(async () => {
      await result.current.setFiles({
        "/notes.md": "keep",
        "/upload/cv.md": "old disk content",
        "/random/new.md": "fresh",
      });
    });

    expect(writeAgentFileMock).not.toHaveBeenCalled();
    expect(client.threads.updateState).toHaveBeenCalledTimes(1);
    expect(client.threads.updateState).toHaveBeenCalledWith("thread-1", {
      values: { files: { "/notes.md": "keep", "/random/new.md": "fresh" } },
    });
  });

  it("routes a changed state file through a single state update", async () => {
    const { result, client } = await setup({ sources: careerSources, agentFiles: [stateFile] });
    await waitFor(() => expect(result.current.files["/notes.md"]).toBe("keep"));

    await act(async () => {
      await result.current.setFiles({ "/notes.md": "edited note" });
    });

    expect(writeAgentFileMock).not.toHaveBeenCalled();
    expect(client.threads.updateState).toHaveBeenCalledWith("thread-1", {
      values: { files: { "/notes.md": "edited note" } },
    });
  });
});

describe("removeFiles", () => {
  it("aggregates a mixed batch: deletes disk files, errors for state and unknown paths", async () => {
    const pdfDiskFile: AgentFile = {
      path: "/upload/cv.pdf",
      content: "pdf",
      encoding: "utf-8",
      source: "disk",
      sourceKey: "/backend/agents/career_agent/upload/cv.pdf",
    };
    const { result } = await setup({
      sources: careerSources,
      agentFiles: [pdfDiskFile, stateFile],
    });
    await waitFor(() => expect(Object.keys(result.current.files)).toHaveLength(2));
    fetchAgentFilesMock.mockClear();

    let outcome: Awaited<ReturnType<typeof result.current.removeFiles>> | undefined;
    await act(async () => {
      outcome = await result.current.removeFiles(["/upload/cv.pdf", "/notes.md", "/ghost.md"]);
    });

    expect(outcome).toEqual({
      deleted: ["/upload/cv.pdf"],
      errors: [
        { path: "/notes.md", reason: "Only disk-backed files can be deleted from the UI" },
        { path: "/ghost.md", reason: "File not found: /ghost.md" },
      ],
    });
    expect(deleteAgentFileMock).toHaveBeenCalledTimes(1);
    expect(deleteAgentFileMock).toHaveBeenCalledWith("/backend/agents/career_agent/upload/cv.pdf");
    // Refreshes the file list after the batch.
    expect(fetchAgentFilesMock).toHaveBeenCalledTimes(1);
  });
});

describe("appendUploadNote", () => {
  it("writes the formatted note into an empty composer and bumps the focus nonce", async () => {
    const { result } = await setup();

    act(() => {
      result.current.appendUploadNote(["cv.pdf", "jd.md"]);
    });

    expect(result.current.input).toBe("Uploaded: cv.pdf, jd.md\n");
    expect(result.current.focusComposerNonce).toBe(1);
  });

  it("appends below existing draft text, collapsing trailing newlines", async () => {
    const { result } = await setup();

    act(() => {
      result.current.setInput("draft note\n\n");
    });
    act(() => {
      result.current.appendUploadNote(["cv.pdf"]);
    });

    expect(result.current.input).toBe("draft note\n\nUploaded: cv.pdf\n");
  });

  it("is a no-op for an empty filename list", async () => {
    const { result } = await setup();

    act(() => {
      result.current.appendUploadNote([]);
    });

    expect(result.current.input).toBe("");
    expect(result.current.focusComposerNonce).toBe(0);
  });
});
