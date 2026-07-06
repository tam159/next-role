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
const NOTE_CONTENT = "# Héllo\n\nRésumé — ✓ unicode round-trip\n";
const PNG_BYTES = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00]);

let tmp: string;
let route: typeof import("./route");

function readReq(repoRel: string): NextRequest {
  const url = new URL("http://localhost/api/files/read");
  url.searchParams.set("path", repoRel);
  return new NextRequest(url);
}

beforeAll(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "files-api-read-"));
  const upload = path.join(tmp, AGENT_ROOT, "upload");
  await fs.mkdir(upload, { recursive: true });
  await fs.writeFile(path.join(upload, "note.md"), NOTE_CONTENT, "utf-8");
  await fs.writeFile(path.join(upload, "pic.png"), PNG_BYTES);
  // REPO_ROOT is computed from cwd at _lib import time: spy first, import after.
  vi.spyOn(process, "cwd").mockReturnValue(path.join(tmp, "frontend"));
  route = await import("./route");
});

afterAll(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("GET /api/files/read", () => {
  it("400 when the path param is missing", async () => {
    const res = await route.GET(new NextRequest("http://localhost/api/files/read"));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Missing 'path'" });
  });

  it("403 for ../ traversal escaping the allowlist", async () => {
    const res = await route.GET(readReq(`${AGENT_ROOT}/upload/../../../../etc/passwd`));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Forbidden path" });
  });

  it("403 for paths outside the allowlist", async () => {
    const res = await route.GET(readReq("backend/other/upload/x.md"));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Forbidden path" });
  });

  it("404 for a missing file inside an allowed dir", async () => {
    const res = await route.GET(readReq(`${AGENT_ROOT}/upload/nope.md`));
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "Not found" });
  });

  it("200 with exact utf-8 content for text files", async () => {
    const res = await route.GET(readReq(`${AGENT_ROOT}/upload/note.md`));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ content: NOTE_CONTENT, encoding: "utf-8" });
  });

  it("200 with base64 content for binary files", async () => {
    const res = await route.GET(readReq(`${AGENT_ROOT}/upload/pic.png`));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { content: string; encoding: string };
    expect(body.encoding).toBe("base64");
    expect(Buffer.from(body.content, "base64").equals(PNG_BYTES)).toBe(true);
  });
});
