import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";

// Stable allowlist for tests, independent of the real career_agent config.
// "missing" is allowlisted but never created on disk (ENOENT walk case).
vi.mock("@/app/config/agentFiles", () => ({
  AGENT_FILE_SOURCES: {
    test_agent: {
      disk: {
        root: "backend/agents/test_agent",
        includeDirs: ["upload", "outputs", "missing"],
      },
    },
  },
}));

const AGENT_ROOT = "backend/agents/test_agent";
const PNG_BYTES = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00]);

type Entry = { path: string; size: number; isBinary: boolean; modifiedAt: string };

let tmp: string;
let route: typeof import("./route");

function listReq(params: Record<string, string>): NextRequest {
  const url = new URL("http://localhost/api/files/list");
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  return new NextRequest(url);
}

async function getFiles(params: Record<string, string>): Promise<Entry[]> {
  const res = await route.GET(listReq(params));
  expect(res.status).toBe(200);
  const body = (await res.json()) as { files: Entry[] };
  return body.files;
}

beforeAll(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "files-api-list-"));
  const upload = path.join(tmp, AGENT_ROOT, "upload");
  const outputs = path.join(tmp, AGENT_ROOT, "outputs");
  await fs.mkdir(path.join(upload, "nested"), { recursive: true });
  await fs.mkdir(path.join(upload, ".hiddendir"), { recursive: true });
  await fs.mkdir(outputs, { recursive: true });
  await fs.writeFile(path.join(upload, "b.md"), "bravo\n");
  await fs.writeFile(path.join(upload, "a.md"), "alpha\n");
  await fs.writeFile(path.join(upload, "pic.png"), PNG_BYTES);
  await fs.writeFile(path.join(upload, ".hidden"), "secret\n");
  await fs.writeFile(path.join(upload, ".hiddendir", "inner.md"), "invisible\n");
  await fs.writeFile(path.join(upload, "nested", "c.md"), "charlie\n");
  await fs.writeFile(path.join(outputs, "report.md"), "report\n");
  // REPO_ROOT is computed from cwd at _lib import time: spy first, import after.
  vi.spyOn(process, "cwd").mockReturnValue(path.join(tmp, "frontend"));
  route = await import("./route");
});

afterAll(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("GET /api/files/list", () => {
  it("400 when root or dirs is missing", async () => {
    const incomplete: Record<string, string>[] = [{}, { root: AGENT_ROOT }, { dirs: "upload" }];
    for (const params of incomplete) {
      const res = await route.GET(listReq(params));
      expect(res.status).toBe(400);
      expect(await res.json()).toEqual({ error: "Missing 'root' or 'dirs'" });
    }
  });

  it("403 for a dir outside the root's includeDirs", async () => {
    const res = await route.GET(listReq({ root: AGENT_ROOT, dirs: "secrets" }));
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({
      error: `Disallowed root/dir: ${AGENT_ROOT}/secrets`,
    });
  });

  it("403 for unknown roots and traversal probes", async () => {
    for (const params of [
      { root: "backend/other", dirs: "upload" },
      { root: "../outside", dirs: "upload" },
      { root: AGENT_ROOT, dirs: "../../etc" },
    ]) {
      const res = await route.GET(listReq(params));
      expect(res.status).toBe(403);
    }
  });

  it("walks recursively, skips dotfiles and dot-dirs, sorts by path", async () => {
    const files = await getFiles({ root: AGENT_ROOT, dirs: "upload" });
    expect(files.map((f) => f.path)).toEqual([
      `/${AGENT_ROOT}/upload/a.md`,
      `/${AGENT_ROOT}/upload/b.md`,
      `/${AGENT_ROOT}/upload/nested/c.md`,
      `/${AGENT_ROOT}/upload/pic.png`,
    ]);
    // .hidden and .hiddendir/inner.md are excluded by the exact list above.
    expect(files.some((f) => f.path.includes(".hidden"))).toBe(false);
  });

  it("reports size, isBinary per extension, and ISO modifiedAt", async () => {
    const files = await getFiles({ root: AGENT_ROOT, dirs: "upload" });
    const byPath = new Map(files.map((f) => [f.path, f]));
    const text = byPath.get(`/${AGENT_ROOT}/upload/a.md`)!;
    const binary = byPath.get(`/${AGENT_ROOT}/upload/pic.png`)!;
    expect(text.size).toBe(6); // "alpha\n"
    expect(text.isBinary).toBe(false);
    expect(binary.size).toBe(PNG_BYTES.length);
    expect(binary.isBinary).toBe(true);
    for (const f of files) {
      expect(new Date(f.modifiedAt).toISOString()).toBe(f.modifiedAt);
    }
  });

  it("returns an empty result (not an error) for an allowed dir missing on disk", async () => {
    const res = await route.GET(listReq({ root: AGENT_ROOT, dirs: "missing" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ files: [] });
  });

  it("merges multiple dirs from a comma-separated dirs param, sorted", async () => {
    const expected = [
      `/${AGENT_ROOT}/outputs/report.md`,
      `/${AGENT_ROOT}/upload/a.md`,
      `/${AGENT_ROOT}/upload/b.md`,
      `/${AGENT_ROOT}/upload/nested/c.md`,
      `/${AGENT_ROOT}/upload/pic.png`,
    ];
    const files = await getFiles({ root: AGENT_ROOT, dirs: "upload,outputs" });
    const paths = files.map((f) => f.path);
    expect(paths).toEqual(expected);
    expect(paths).toEqual([...paths].sort((a, b) => a.localeCompare(b)));
    // Whitespace around segments is trimmed.
    const spaced = await getFiles({ root: AGENT_ROOT, dirs: " upload , outputs " });
    expect(spaced.map((f) => f.path)).toEqual(expected);
  });
});
