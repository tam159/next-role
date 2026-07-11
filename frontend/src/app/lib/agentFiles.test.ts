import type { Client } from "@langchain/langgraph-sdk";
import { AGENT_FILE_SOURCES, type AgentFileSources } from "@/app/config/agentFiles";
import {
  type AgentFile,
  fetchAgentFiles,
  filesApiUrl,
  getAgentFileSources,
  isBinaryPath,
  isImagePath,
  resolveStoreLocation,
  writeAgentFile,
} from "./agentFiles";

const careerStoreCfg = AGENT_FILE_SOURCES.career_agent.store!;

function makeClient() {
  return {
    store: {
      searchItems: vi.fn(async (_ns: string[], _opts: { limit: number }) => ({
        items: [] as unknown[],
      })),
      putItem: vi.fn(async () => undefined),
    },
    threads: {
      updateState: vi.fn(async () => undefined),
    },
  };
}
type StubClient = ReturnType<typeof makeClient>;
const asClient = (c: StubClient) => c as unknown as Client;

function jsonResponse(
  data: unknown,
  init: { ok?: boolean; status?: number; text?: string } = {}
): { ok: boolean; status: number; json: () => Promise<unknown>; text: () => Promise<string> } {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => data,
    text: async () => init.text ?? "",
  };
}

function file(over: Partial<AgentFile> & { sourceKey: string; source: AgentFile["source"] }) {
  return {
    path: over.sourceKey,
    content: "content",
    encoding: "utf-8" as const,
    ...over,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("isImagePath", () => {
  it("recognizes image extensions", () => {
    expect(isImagePath("/research/chart.png")).toBe(true);
    expect(isImagePath("photo.webp")).toBe(true);
  });

  it("is case-insensitive", () => {
    expect(isImagePath("/research/CHART.PNG")).toBe(true);
    expect(isImagePath("pic.JpEg")).toBe(true);
  });

  it("rejects non-image and extension-less paths", () => {
    expect(isImagePath("/processed/resume.pdf")).toBe(false);
    expect(isImagePath("/processed/notes.md")).toBe(false);
    expect(isImagePath("no-extension")).toBe(false);
    expect(isImagePath("")).toBe(false);
  });
});

describe("isBinaryPath", () => {
  it("includes images plus documents and archives", () => {
    expect(isBinaryPath("/upload/cv.pdf")).toBe(true);
    expect(isBinaryPath("/upload/cv.DOCX")).toBe(true);
    expect(isBinaryPath("/research/chart.png")).toBe(true);
    expect(isBinaryPath("archive.tar.gz")).toBe(true);
  });

  it("rejects text and extension-less paths", () => {
    expect(isBinaryPath("/processed/notes.md")).toBe(false);
    expect(isBinaryPath("no-extension")).toBe(false);
  });
});

describe("filesApiUrl", () => {
  it("returns a relative URL when no deployment URL is configured", () => {
    // Node test env: no window, no NEXT_PUBLIC_* vars -> DEFAULT_CONFIG is null.
    expect(filesApiUrl("/files/list?x=1")).toBe("/files/list?x=1");
  });

  it("prefixes the configured deployment URL, trimming trailing slashes", async () => {
    vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", "http://127.0.0.1:8129/");
    vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", "career_agent");
    vi.resetModules();
    const fresh = await import("./agentFiles");
    expect(fresh.filesApiUrl("/files/read?path=%2Fupload%2Fcv.pdf")).toBe(
      "http://127.0.0.1:8129/files/read?path=%2Fupload%2Fcv.pdf"
    );
    vi.unstubAllEnvs();
    vi.resetModules();
  });
});

describe("getAgentFileSources", () => {
  it("returns the configured sources for a known graph id", () => {
    expect(getAgentFileSources("career_agent")).toBe(AGENT_FILE_SOURCES.career_agent);
  });

  it("returns undefined for unknown, null, or undefined graph ids", () => {
    expect(getAgentFileSources("mystery_agent")).toBeUndefined();
    expect(getAgentFileSources(null)).toBeUndefined();
    expect(getAgentFileSources(undefined)).toBeUndefined();
  });
});

describe("resolveStoreLocation", () => {
  it("maps a prefixed path to namespace segments plus a leading-slash key", () => {
    expect(resolveStoreLocation(careerStoreCfg, "/processed/resume.md")).toEqual({
      namespace: ["career_agent", "processed"],
      key: "/resume.md",
    });
  });

  it("keeps nested subdirectories in the key", () => {
    expect(resolveStoreLocation(careerStoreCfg, "/interview_coach/acme/questions.md")).toEqual({
      namespace: ["career_agent", "interview_coach"],
      key: "/acme/questions.md",
    });
  });

  it("resolves an exact prefix match (no remainder) to key '/'", () => {
    expect(resolveStoreLocation(careerStoreCfg, "/processed")).toEqual({
      namespace: ["career_agent", "processed"],
      key: "/",
    });
  });

  it("requires a segment boundary after the prefix", () => {
    expect(resolveStoreLocation(careerStoreCfg, "/processedx/resume.md")).toBeNull();
  });

  it("returns null when no configured prefix matches", () => {
    expect(resolveStoreLocation(careerStoreCfg, "/unknown/file.md")).toBeNull();
  });

  it("prefers the longest matching prefix regardless of config order", () => {
    const cfg: NonNullable<AgentFileSources["store"]> = {
      namespacePrefix: ["agent"],
      pathPrefixes: ["/a/", "/a/b/"],
    };
    expect(resolveStoreLocation(cfg, "/a/b/c.md")).toEqual({
      namespace: ["agent", "a", "b"],
      key: "/c.md",
    });
    expect(resolveStoreLocation(cfg, "/a/x.md")).toEqual({
      namespace: ["agent", "a"],
      key: "/x.md",
    });
  });
});

describe("fetchAgentFiles", () => {
  it("maps state files only when the graph has no configured sources", async () => {
    const client = makeClient();
    const result = await fetchAgentFiles({
      client: asClient(client),
      graphId: null,
      stateFiles: {
        "/notes/b.md": "raw string",
        "/notes/a.md": { content: ["line 1", "line 2"] },
        "/notes/c.md": { content: "inner" },
        "/notes/d.md": 42,
        "/notes/e.md": { content: null },
      },
    });

    // No timestamps anywhere -> pure alphabetical order.
    expect(result.map((f) => f.path)).toEqual([
      "/notes/a.md",
      "/notes/b.md",
      "/notes/c.md",
      "/notes/d.md",
      "/notes/e.md",
    ]);
    expect(result.map((f) => f.content)).toEqual([
      "line 1\nline 2",
      "raw string",
      "inner",
      "42",
      "",
    ]);
    for (const f of result) {
      expect(f.source).toBe("state");
      expect(f.encoding).toBe("utf-8");
      expect(f.sourceKey).toBe(f.path);
      expect(f.modifiedAt).toBeUndefined();
    }
    expect(client.store.searchItems).not.toHaveBeenCalled();
  });

  it("merges store, artifact, and state files with state winning path collisions", async () => {
    const client = makeClient();
    client.store.searchItems.mockImplementation(async (namespace: string[]) => {
      if (namespace.join("/") === "career_agent/processed") {
        return {
          items: [
            {
              key: "resume.md",
              value: { content: "store resume" },
              updatedAt: "2026-01-02T00:00:00.000Z",
            },
            {
              key: "/notes.md",
              value: { content: ["n1", "n2"] },
              updated_at: "2026-01-01T00:00:00.000Z",
            },
          ],
        };
      }
      if (namespace.join("/") === "career_agent/research") {
        return { items: [{ key: "chart.png", value: { content: "AAAA", encoding: "base64" } }] };
      }
      return { items: [] };
    });

    const fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith("/files/list")) {
        return jsonResponse({
          files: [
            {
              path: "/upload/cv.pdf",
              isBinary: true,
              modifiedAt: "2026-01-03T00:00:00.000Z",
            },
            { path: "/upload/broken.md", isBinary: false },
          ],
        });
      }
      if (url.includes(encodeURIComponent("cv.pdf"))) {
        return jsonResponse({ content: "JVBERi0=", encoding: "base64" });
      }
      // broken.md read fails -> that file is silently dropped.
      return jsonResponse(null, { ok: false, status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchAgentFiles({
      client: asClient(client),
      graphId: "career_agent",
      stateFiles: {
        "/processed/resume.md": "state resume",
        "/state/todo.md": "todo",
      },
    });

    // Newest first, then alphabetical among timestamp-less files.
    expect(result.map((f) => f.path)).toEqual([
      "/upload/cv.pdf",
      "/processed/notes.md",
      "/processed/resume.md",
      "/research/chart.png",
      "/state/todo.md",
    ]);

    const byPath = new Map(result.map((f) => [f.path, f]));

    // State wins the collision on /processed/resume.md.
    expect(byPath.get("/processed/resume.md")).toMatchObject({
      source: "state",
      content: "state resume",
    });
    // State files carry no timestamp (no modifiedAt key at all).
    expect(byPath.get("/processed/resume.md")?.modifiedAt).toBeUndefined();

    // Store item: array content joined, snake_case updated_at parsed.
    expect(byPath.get("/processed/notes.md")).toMatchObject({
      source: "store",
      content: "n1\nn2",
      encoding: "utf-8",
      sourceKey: "/processed/notes.md",
      modifiedAt: Date.parse("2026-01-01T00:00:00.000Z"),
    });

    // Store item without a timestamp keeps modifiedAt undefined; base64 kept.
    expect(byPath.get("/research/chart.png")).toMatchObject({
      source: "store",
      content: "AAAA",
      encoding: "base64",
      modifiedAt: undefined,
    });

    // Artifact file: virtual path is both the path and the write-back key.
    expect(byPath.get("/upload/cv.pdf")).toMatchObject({
      source: "artifact",
      content: "JVBERi0=",
      encoding: "base64",
      sourceKey: "/upload/cv.pdf",
      modifiedAt: Date.parse("2026-01-03T00:00:00.000Z"),
    });

    // The failed artifact read is dropped entirely.
    expect(byPath.has("/upload/broken.md")).toBe(false);

    // One store search per configured path prefix, with the page limit.
    expect(client.store.searchItems).toHaveBeenCalledTimes(careerStoreCfg.pathPrefixes.length);
    expect(client.store.searchItems).toHaveBeenCalledWith(["career_agent", "processed"], {
      limit: 200,
    });
    expect(client.store.searchItems).toHaveBeenCalledWith(["career_agent", "memory"], {
      limit: 200,
    });

    // Artifact list call carries the configured virtual prefixes.
    const listUrl = String(fetchMock.mock.calls[0][0]);
    expect(listUrl).toContain("/files/list?");
    expect(listUrl).toContain(
      `prefixes=${encodeURIComponent("/upload/,/tailored_resume/,/interview_battlecard/")}`
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/files/read?path=${encodeURIComponent("/upload/cv.pdf")}`
    );
  });

  it("still returns other sources when the artifact fetch rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const client = makeClient();
    client.store.searchItems.mockImplementation(async (namespace: string[]) => {
      if (namespace.join("/") === "career_agent/memory") {
        return { items: [{ key: "profile.md", value: { content: "profile" } }] };
      }
      return { items: [] };
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new Error("network down")))
    );

    const result = await fetchAgentFiles({
      client: asClient(client),
      graphId: "career_agent",
      stateFiles: { "/state/todo.md": "todo" },
    });

    expect(result.map((f) => f.path)).toEqual(["/memory/profile.md", "/state/todo.md"]);
    expect(warn).toHaveBeenCalledWith("artifact fetch failed", expect.any(Error));
  });

  it("treats a non-ok artifact list response as an empty artifact source", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const client = makeClient();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(null, { ok: false, status: 500, text: "boom" }))
    );

    const result = await fetchAgentFiles({
      client: asClient(client),
      graphId: "career_agent",
      stateFiles: { "/state/todo.md": "todo" },
    });

    expect(result.map((f) => f.path)).toEqual(["/state/todo.md"]);
    expect(warn).toHaveBeenCalledWith("artifact list failed", 500, "boom");
  });

  it("still returns artifact and state files when every store search rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const client = makeClient();
    client.store.searchItems.mockRejectedValue(new Error("store down"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/files/list")) {
          return jsonResponse({
            files: [{ path: "/upload/cv.pdf", isBinary: true }],
          });
        }
        return jsonResponse({ content: "artifact content", encoding: "utf-8" });
      })
    );

    const result = await fetchAgentFiles({
      client: asClient(client),
      graphId: "career_agent",
      stateFiles: {},
    });

    expect(result.map((f) => f.path)).toEqual(["/upload/cv.pdf"]);
    expect(warn).toHaveBeenCalled();
  });
});

describe("writeAgentFile", () => {
  it("routes store files to putItem with the resolved namespace and key", async () => {
    const client = makeClient();
    await writeAgentFile({
      client: asClient(client),
      threadId: null,
      graphId: "career_agent",
      file: file({ source: "store", sourceKey: "/processed/resume.md", content: "hello" }),
    });
    expect(client.store.putItem).toHaveBeenCalledExactlyOnceWith(
      ["career_agent", "processed"],
      "/resume.md",
      { content: "hello", encoding: "utf-8" }
    );
  });

  it("throws for a store file when the agent has no store config", async () => {
    const client = makeClient();
    await expect(
      writeAgentFile({
        client: asClient(client),
        threadId: null,
        graphId: "mystery_agent",
        file: file({ source: "store", sourceKey: "/processed/resume.md" }),
      })
    ).rejects.toThrow("Store backend not configured for this agent");
    expect(client.store.putItem).not.toHaveBeenCalled();
  });

  it("throws for a store file whose path matches no configured prefix", async () => {
    const client = makeClient();
    await expect(
      writeAgentFile({
        client: asClient(client),
        threadId: null,
        graphId: "career_agent",
        file: file({ source: "store", sourceKey: "/elsewhere/resume.md" }),
      })
    ).rejects.toThrow(/No matching store pathPrefix for \/elsewhere\/resume\.md/);
    expect(client.store.putItem).not.toHaveBeenCalled();
  });

  it("routes artifact files to PUT /files/write with path, content, and encoding", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const client = makeClient();

    await writeAgentFile({
      client: asClient(client),
      threadId: null,
      graphId: "career_agent",
      file: file({
        source: "artifact",
        sourceKey: "/upload/cv.md",
        content: "artifact content",
      }),
    });

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/files/write", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: "/upload/cv.md",
        content: "artifact content",
        encoding: "utf-8",
      }),
    });
  });

  it("throws with status and body when the artifact write fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(null, { ok: false, status: 500, text: "boom" }))
    );
    await expect(
      writeAgentFile({
        client: asClient(makeClient()),
        threadId: null,
        graphId: "career_agent",
        file: file({ source: "artifact", sourceKey: "/upload/cv.md" }),
      })
    ).rejects.toThrow("Artifact write failed: 500 boom");
  });

  it("routes state files to threads.updateState, merging into the files map", async () => {
    const client = makeClient();
    await writeAgentFile({
      client: asClient(client),
      threadId: "thread-1",
      graphId: "career_agent",
      file: file({ source: "state", sourceKey: "/notes/todo.md", content: "new content" }),
      stateFiles: { "/existing.md": "old" },
    });
    expect(client.threads.updateState).toHaveBeenCalledExactlyOnceWith("thread-1", {
      values: { files: { "/existing.md": "old", "/notes/todo.md": "new content" } },
    });
  });

  it("writes a state file even when no stateFiles map is provided", async () => {
    const client = makeClient();
    await writeAgentFile({
      client: asClient(client),
      threadId: "thread-1",
      graphId: null,
      file: file({ source: "state", sourceKey: "/only.md", content: "solo" }),
    });
    expect(client.threads.updateState).toHaveBeenCalledExactlyOnceWith("thread-1", {
      values: { files: { "/only.md": "solo" } },
    });
  });

  it("throws for a state file without a threadId", async () => {
    const client = makeClient();
    await expect(
      writeAgentFile({
        client: asClient(client),
        threadId: null,
        graphId: "career_agent",
        file: file({ source: "state", sourceKey: "/notes/todo.md" }),
      })
    ).rejects.toThrow("No threadId for state write");
    expect(client.threads.updateState).not.toHaveBeenCalled();
  });
});
