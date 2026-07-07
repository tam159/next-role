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

function delReq(repoRel?: string): NextRequest {
  const url = new URL("http://localhost/api/files/delete");
  if (repoRel !== undefined) url.searchParams.set("path", repoRel);
  return new NextRequest(url, { method: "DELETE" });
}

beforeAll(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "files-api-delete-"));
  await fs.mkdir(path.join(tmp, AGENT_ROOT, "upload"), { recursive: true });
  // REPO_ROOT is computed from cwd at _lib import time: spy first, import after.
  vi.spyOn(process, "cwd").mockReturnValue(path.join(tmp, "frontend"));
  route = await import("./route");
});

afterAll(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("DELETE /api/files/delete", () => {
  it("400 when the path param is missing", async () => {
    const res = await route.DELETE(delReq());
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Missing 'path'" });
  });

  it("403 for paths outside the allowlist", async () => {
    for (const repoRel of [
      `${AGENT_ROOT}/upload/../../../../etc/passwd`,
      "backend/other/upload/x.md",
    ]) {
      const res = await route.DELETE(delReq(repoRel));
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ error: "Forbidden path" });
    }
  });

  it("404 for a missing file inside an allowed dir", async () => {
    const res = await route.DELETE(delReq(`${AGENT_ROOT}/upload/ghost.md`));
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "Not found" });
  });

  it("400 when the target is a directory (EISDIR)", async () => {
    // fs.unlink on a directory yields EISDIR on Linux but EPERM on macOS (where
    // the real call would fall through to the 500 branch), so the EISDIR branch
    // is exercised deterministically via a mock.
    const dirRel = `${AGENT_ROOT}/upload/somedir`;
    await fs.mkdir(path.join(tmp, dirRel), { recursive: true });
    const eisdir = Object.assign(new Error("EISDIR: illegal operation on a directory"), {
      code: "EISDIR",
    });
    const spy = vi.spyOn(fs, "unlink").mockRejectedValueOnce(eisdir);
    try {
      const res = await route.DELETE(delReq(dirRel));
      expect(res.status).toBe(400);
      expect(await res.json()).toEqual({ error: "Path is a directory" });
      expect(spy).toHaveBeenCalledWith(path.join(tmp, dirRel));
    } finally {
      spy.mockRestore();
    }
  });

  it("200 deletes the file, and a second delete 404s", async () => {
    const repoRel = `${AGENT_ROOT}/upload/doomed.md`;
    const abs = path.join(tmp, repoRel);
    await fs.writeFile(abs, "goodbye\n");

    const res = await route.DELETE(delReq(repoRel));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    await expect(fs.access(abs)).rejects.toThrow();

    const again = await route.DELETE(delReq(repoRel));
    expect(again.status).toBe(404);
  });
});
