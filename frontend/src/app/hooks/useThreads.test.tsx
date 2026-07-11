import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { Client } from "@langchain/langgraph-sdk";
import { getConfig } from "@/lib/config";
import { useThreads } from "@/app/hooks/useThreads";

const { searchMock } = vi.hoisted(() => ({ searchMock: vi.fn() }));

vi.mock("@langchain/langgraph-sdk", () => ({
  // Constructed with `new`, so the implementation must be a `function` (not an
  // arrow) whose returned object becomes the instance.
  Client: vi.fn(function () {
    return { threads: { search: searchMock } };
  }),
}));
vi.mock("@/lib/config", () => ({ getConfig: vi.fn() }));

const ClientMock = vi.mocked(Client);
const getConfigMock = vi.mocked(getConfig);

const UUID_ASSISTANT = "0f1e2d3c-4b5a-6978-8899-aabbccddeeff";

const baseConfig = {
  deploymentUrl: "http://deploy:2024",
  assistantId: "career_agent",
};

function makeWrapper() {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
    );
  };
}

function makeThread(overrides: Record<string, unknown> = {}) {
  return {
    thread_id: "00000000-0000-4000-8000-000000000000",
    updated_at: "2026-07-01T10:00:00.000Z",
    status: "idle",
    values: { messages: [] },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // The key builder falls back to this env var when config has no key.
  vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("useThreads", () => {
  it("returns no data and never constructs a client when config is null", async () => {
    getConfigMock.mockReturnValue(null);

    const { result } = renderHook(() => useThreads({}), { wrapper: makeWrapper() });
    await act(async () => {});

    expect(result.current.data).toBeUndefined();
    expect(ClientMock).not.toHaveBeenCalled();
    expect(searchMock).not.toHaveBeenCalled();
  });

  it("filters by assistant_id metadata for UUID assistant ids and sends the api key header", async () => {
    getConfigMock.mockReturnValue({
      ...baseConfig,
      assistantId: UUID_ASSISTANT,
      langsmithApiKey: "sk-ls",
    });
    searchMock.mockResolvedValue([]);

    renderHook(() => useThreads({ status: "idle" }), { wrapper: makeWrapper() });
    await waitFor(() => expect(searchMock).toHaveBeenCalled());

    expect(ClientMock).toHaveBeenCalledWith({
      apiUrl: "http://deploy:2024",
      defaultHeaders: { "X-Api-Key": "sk-ls" },
      onRequest: expect.any(Function),
    });
    expect(searchMock).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      sortBy: "updated_at",
      sortOrder: "desc",
      status: "idle",
      metadata: { assistant_id: UUID_ASSISTANT },
    });
  });

  it("omits the metadata filter for graph-name assistant ids and sends empty headers", async () => {
    getConfigMock.mockReturnValue(baseConfig);
    searchMock.mockResolvedValue([]);

    renderHook(() => useThreads({}), { wrapper: makeWrapper() });
    await waitFor(() => expect(searchMock).toHaveBeenCalled());

    expect(ClientMock).toHaveBeenCalledWith({
      apiUrl: "http://deploy:2024",
      defaultHeaders: {},
      onRequest: expect.any(Function),
    });
    const args = searchMock.mock.calls[0][0];
    expect(args).not.toHaveProperty("metadata");
    expect(args).toMatchObject({ limit: 20, offset: 0, sortBy: "updated_at", sortOrder: "desc" });
  });

  it("maps threads to titles and descriptions with truncation", async () => {
    getConfigMock.mockReturnValue(baseConfig);
    searchMock.mockResolvedValue([
      makeThread({
        thread_id: "11111111-1111-4111-8111-111111111111",
        status: "idle",
        values: {
          messages: [
            { type: "human", content: "a".repeat(60) },
            { type: "ai", content: "b".repeat(120) },
          ],
        },
      }),
      makeThread({
        thread_id: "22222222-2222-4222-8222-222222222222",
        status: "busy",
        updated_at: "2026-07-02T08:30:00.000Z",
        values: {
          messages: [
            { type: "ai", content: [{ type: "text", text: "Block reply" }] },
            { type: "human", content: [{ type: "text", text: "Block title" }] },
          ],
        },
      }),
    ]);

    const { result } = renderHook(() => useThreads({}), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data?.[0]).toHaveLength(2));

    const [first, second] = result.current.data![0];
    expect(first).toEqual({
      id: "11111111-1111-4111-8111-111111111111",
      updatedAt: new Date("2026-07-01T10:00:00.000Z"),
      status: "idle",
      title: "a".repeat(50) + "...",
      description: "b".repeat(100),
      assistantId: "career_agent",
    });
    // Array-of-blocks content resolves through the first block's text; short
    // titles get no ellipsis and the ai description is never suffixed.
    expect(second.title).toBe("Block title");
    expect(second.description).toBe("Block reply");
    expect(second.status).toBe("busy");
    expect(second.updatedAt).toEqual(new Date("2026-07-02T08:30:00.000Z"));
  });

  it("falls back per-thread when values are malformed or missing", async () => {
    getConfigMock.mockReturnValue(baseConfig);
    searchMock.mockResolvedValue([
      // values present but no messages array -> mapping throws -> id-based title.
      makeThread({ thread_id: "deadbeef-0000-4000-8000-000000000000", values: {} }),
      // values missing entirely -> guard short-circuits -> default title.
      makeThread({ thread_id: "feedface-0000-4000-8000-000000000000", values: null }),
    ]);

    const { result } = renderHook(() => useThreads({}), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data?.[0]).toHaveLength(2));

    const [broken, missing] = result.current.data![0];
    expect(broken.title).toBe("Thread deadbeef");
    expect(broken.description).toBe("");
    expect(missing.title).toBe("Untitled Thread");
    expect(missing.description).toBe("");
  });

  it("advances the offset by page size and stops paginating after an empty page", async () => {
    getConfigMock.mockReturnValue(baseConfig);
    searchMock.mockImplementation(async (args: { offset: number }) =>
      args.offset === 0
        ? [
            makeThread({ thread_id: "11111111-1111-4111-8111-111111111111" }),
            makeThread({ thread_id: "22222222-2222-4222-8222-222222222222" }),
          ]
        : []
    );

    const { result } = renderHook(() => useThreads({ limit: 2 }), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data?.[0]).toHaveLength(2));

    await act(async () => {
      await result.current.setSize(2);
    });
    await waitFor(() => expect(result.current.data).toHaveLength(2));
    expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ limit: 2, offset: 2 }));

    // Page 1 came back empty, so the page-2 key is null: no offset-4 request.
    await act(async () => {
      await result.current.setSize(3);
    });
    await act(async () => {});
    const offsets = searchMock.mock.calls.map((c) => c[0].offset);
    expect(offsets).not.toContain(4);
    expect(result.current.data).toHaveLength(2);
  });
});
