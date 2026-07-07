import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";

// Stable allowlist for tests, independent of the real career_agent config.
vi.mock("@/app/config/agentFiles", () => ({
  AGENT_FILE_SOURCES: {
    test_agent: {
      disk: { root: "backend/agents/test_agent", includeDirs: ["upload", "outputs"] },
    },
  },
}));

const AGENT_ROOT = "backend/agents/test_agent";

let tmp: string;
let route: typeof import("./route");

/** Build a PUT request; pass a string to send a raw (possibly invalid-JSON) body. */
function writeReq(body: unknown): NextRequest {
  return new NextRequest("http://localhost/api/files/write", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

beforeAll(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "files-api-write-"));
  await fs.mkdir(path.join(tmp, AGENT_ROOT, "upload"), { recursive: true });
  // REPO_ROOT is computed from cwd at _lib import time: spy first, import after.
  vi.spyOn(process, "cwd").mockReturnValue(path.join(tmp, "frontend"));
  route = await import("./route");
});

afterAll(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("PUT /api/files/write", () => {
  it("400 for an invalid JSON body", async () => {
    const res = await route.PUT(writeReq("not json{"));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Invalid JSON" });
  });

  it("400 when path or content is missing, or content is not a string", async () => {
    for (const body of [
      { content: "x" },
      { path: `${AGENT_ROOT}/upload/x.md` },
      { path: `${AGENT_ROOT}/upload/x.md`, content: 42 },
    ]) {
      const res = await route.PUT(writeReq(body));
      expect(res.status).toBe(400);
      expect(await res.json()).toEqual({ error: "Missing 'path' or 'content'" });
    }
  });

  it("403 for ../ traversal escaping the allowlist", async () => {
    const res = await route.PUT(
      writeReq({ path: `${AGENT_ROOT}/upload/../../../../etc/pwn`, content: "x" })
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Forbidden path" });
  });

  it("403 for paths outside the allowlist", async () => {
    const res = await route.PUT(writeReq({ path: "backend/other/upload/x.md", content: "x" }));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Forbidden path" });
  });

  it("200 writes utf-8 content, creating nested dirs", async () => {
    const repoRel = `${AGENT_ROOT}/upload/nested/deep/new.md`;
    const content = "# Écrit — ✓\n";
    const res = await route.PUT(writeReq({ path: repoRel, content }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(await fs.readFile(path.join(tmp, repoRel), "utf-8")).toBe(content);
  });

  it("200 decodes base64 content to exact bytes", async () => {
    const repoRel = `${AGENT_ROOT}/outputs/raw.bin`;
    const bytes = Buffer.from([0x00, 0x01, 0x02, 0xfa, 0xff, 0x80]);
    const res = await route.PUT(
      writeReq({ path: repoRel, content: bytes.toString("base64"), encoding: "base64" })
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    const onDisk = await fs.readFile(path.join(tmp, repoRel));
    expect(onDisk.equals(bytes)).toBe(true);
  });
});
