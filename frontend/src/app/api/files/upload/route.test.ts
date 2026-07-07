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
const UPLOAD_DIR = `${AGENT_ROOT}/upload`;

let tmp: string;
let route: typeof import("./route");

function uploadReq(form: FormData): NextRequest {
  return new NextRequest("http://localhost/api/files/upload", { method: "POST", body: form });
}

/** Build a multipart form; the dir goes in the "path" field, files in "file". */
function makeForm(dir: string | null, files: File[]): FormData {
  const form = new FormData();
  if (dir !== null) form.append("path", dir);
  for (const file of files) form.append("file", file);
  return form;
}

beforeAll(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "files-api-upload-"));
  await fs.mkdir(path.join(tmp, AGENT_ROOT, "upload"), { recursive: true });
  // REPO_ROOT is computed from cwd at _lib import time: spy first, import after.
  vi.spyOn(process, "cwd").mockReturnValue(path.join(tmp, "frontend"));
  route = await import("./route");
});

afterAll(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("POST /api/files/upload", () => {
  it("400 for a non-multipart body", async () => {
    const res = await route.POST(
      new NextRequest("http://localhost/api/files/upload", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: UPLOAD_DIR }),
      })
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Expected multipart/form-data" });
  });

  it("400 when the 'path' (target dir) field is missing or empty", async () => {
    for (const dir of [null, ""]) {
      const res = await route.POST(uploadReq(makeForm(dir, [new File(["x"], "ok.md")])));
      expect(res.status).toBe(400);
      expect(await res.json()).toEqual({ error: "Missing 'path' field" });
    }
  });

  it("400 when no files are provided (string entries don't count)", async () => {
    const res = await route.POST(uploadReq(makeForm(UPLOAD_DIR, [])));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "No files provided" });

    const formWithString = makeForm(UPLOAD_DIR, []);
    formWithString.append("file", "not-a-file");
    const res2 = await route.POST(uploadReq(formWithString));
    expect(res2.status).toBe(400);
    expect(await res2.json()).toEqual({ error: "No files provided" });
  });

  it("rejects disallowed extensions per file", async () => {
    const res = await route.POST(uploadReq(makeForm(UPLOAD_DIR, [new File(["mz"], "virus.exe")])));
    expect(res.status).toBe(400); // all files rejected → 400
    expect(await res.json()).toEqual({
      uploaded: [],
      errors: [{ name: "virus.exe", reason: "Unsupported extension: .exe" }],
    });
  });

  it("rejects files over the 10 MB limit", async () => {
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.md");
    const res = await route.POST(uploadReq(makeForm(UPLOAD_DIR, [big])));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      uploaded: [],
      errors: [{ name: "big.md", reason: "File exceeds 10 MB limit" }],
    });
  });

  it("rejects filenames with path separators or a leading dot", async () => {
    // Names must carry an allowed extension to get past the extension check,
    // which runs before the filename check.
    const res = await route.POST(
      uploadReq(
        makeForm(UPLOAD_DIR, [
          new File(["a"], "evil/../x.md"),
          new File(["b"], "evil\\x.md"),
          new File(["c"], ".hidden.md"),
        ])
      )
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      uploaded: [],
      errors: [
        { name: "evil/../x.md", reason: "Invalid filename" },
        { name: "evil\\x.md", reason: "Invalid filename" },
        { name: ".hidden.md", reason: "Invalid filename" },
      ],
    });
  });

  it("checks extension before filename: a bare dotfile reads as an extension", async () => {
    const res = await route.POST(uploadReq(makeForm(UPLOAD_DIR, [new File(["x"], ".hidden")])));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      uploaded: [],
      errors: [{ name: ".hidden", reason: "Unsupported extension: .hidden" }],
    });
  });

  it("rejects target dirs outside the allowlist with a per-file error", async () => {
    for (const dir of ["backend/other/upload", `${AGENT_ROOT}/upload/../../../../etc`]) {
      const res = await route.POST(uploadReq(makeForm(dir, [new File(["x"], "ok.md")])));
      expect(res.status).toBe(400); // per-file "Forbidden path", not a top-level 403
      expect(await res.json()).toEqual({
        uploaded: [],
        errors: [{ name: "ok.md", reason: "Forbidden path" }],
      });
    }
  });

  it("200 for a mixed batch: uploads good files, reports errors for the rest", async () => {
    const content = "hello upload";
    const res = await route.POST(
      uploadReq(
        // Trailing slash on the dir is trimmed before building the target path.
        makeForm(`${UPLOAD_DIR}/`, [new File([content], "ok.md"), new File(["mz"], "virus.exe")])
      )
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      uploaded: [{ path: `${UPLOAD_DIR}/ok.md`, size: content.length }],
      errors: [{ name: "virus.exe", reason: "Unsupported extension: .exe" }],
    });
    expect(await fs.readFile(path.join(tmp, UPLOAD_DIR, "ok.md"), "utf-8")).toBe(content);
  });
});
