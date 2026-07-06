import path from "node:path";

// AGENT_FILE_SOURCES is mocked so these tests stay stable if the real career_agent
// config evolves. _lib reads the allowlist at module scope and computes REPO_ROOT
// from process.cwd() at import time — hence the cwd spy in beforeAll and the
// dynamic import (never a static import of the module under test).
vi.mock("@/app/config/agentFiles", () => ({
  AGENT_FILE_SOURCES: {
    test_agent: {
      disk: { root: "backend/agents/test_agent", includeDirs: ["upload", "outputs"] },
    },
  },
}));

// resolveSafe/resolveDir are pure path math — no filesystem is touched, so the
// repo root can be entirely virtual. Documented limitation (no test): symlinks
// are NOT resolved (no realpath); containment is checked on the lexical path only.
const FAKE_REPO = path.join(path.sep, "virtual-nextrole-repo");
const AGENT_ROOT = "backend/agents/test_agent";
const UPLOAD_ABS = path.join(FAKE_REPO, AGENT_ROOT, "upload");
const OUTPUTS_ABS = path.join(FAKE_REPO, AGENT_ROOT, "outputs");

let lib: typeof import("./_lib");

beforeAll(async () => {
  vi.spyOn(process, "cwd").mockReturnValue(path.join(FAKE_REPO, "frontend"));
  lib = await import("./_lib");
});

afterAll(() => {
  vi.restoreAllMocks();
});

describe("REPO_ROOT", () => {
  it("resolves to the parent of cwd at import time", () => {
    expect(lib.REPO_ROOT).toBe(FAKE_REPO);
  });
});

describe("resolveSafe", () => {
  it("resolves a path inside an allowed bucket to an absolute path", () => {
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload/x.md`)).toBe(path.join(UPLOAD_ABS, "x.md"));
  });

  it("resolves nested paths inside an allowed bucket", () => {
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload/a/b/c.md`)).toBe(
      path.join(UPLOAD_ABS, "a", "b", "c.md")
    );
  });

  it("rejects ../ traversal that escapes the allowlist", () => {
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload/../../../../etc/passwd`)).toBeNull();
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload/../../../../../../../etc/passwd`)).toBeNull();
    expect(lib.resolveSafe("/etc/passwd")).toBeNull();
  });

  it("allows a path that uses .. but lands inside another allowed bucket", () => {
    // Traversal is judged by the resolved destination, not by the presence of "..".
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload/../outputs/x.md`)).toBe(
      path.join(OUTPUTS_ABS, "x.md")
    );
  });

  it("rejects sibling dirs sharing the allowed dir as a string prefix", () => {
    // Guarded by startsWith(allowed + path.sep), not bare startsWith(allowed).
    expect(lib.resolveSafe(`${AGENT_ROOT}/uploadX/f.md`)).toBeNull();
    expect(lib.resolveSafe(`${AGENT_ROOT}/outputs-evil/f.md`)).toBeNull();
  });

  it("allows a path exactly equal to the allowed dir", () => {
    expect(lib.resolveSafe(`${AGENT_ROOT}/upload`)).toBe(UPLOAD_ABS);
  });

  it("strips leading slashes", () => {
    expect(lib.resolveSafe(`/${AGENT_ROOT}/upload/x.md`)).toBe(path.join(UPLOAD_ABS, "x.md"));
    expect(lib.resolveSafe(`///${AGENT_ROOT}/upload/x.md`)).toBe(path.join(UPLOAD_ABS, "x.md"));
  });

  it("rejects empty and non-string input", () => {
    expect(lib.resolveSafe("")).toBeNull();
    expect(lib.resolveSafe(42 as unknown as string)).toBeNull();
    expect(lib.resolveSafe(null as unknown as string)).toBeNull();
    expect(lib.resolveSafe(undefined as unknown as string)).toBeNull();
  });

  it("rejects paths under roots that are not allowlisted", () => {
    expect(lib.resolveSafe("backend/other/upload/x.md")).toBeNull();
  });

  it("rejects paths under an allowed root but outside its includeDirs", () => {
    expect(lib.resolveSafe(`${AGENT_ROOT}/secrets/x.md`)).toBeNull();
    expect(lib.resolveSafe(`${AGENT_ROOT}/x.md`)).toBeNull();
  });
});

describe("resolveDir", () => {
  it("resolves an allowlisted root/dir bucket", () => {
    expect(lib.resolveDir(AGENT_ROOT, "upload")).toBe(UPLOAD_ABS);
    expect(lib.resolveDir(AGENT_ROOT, "outputs")).toBe(OUTPUTS_ABS);
  });

  it("rejects roots not registered in any agent config", () => {
    expect(lib.resolveDir("backend/agents/unknown", "upload")).toBeNull();
  });

  it("rejects roots that escape REPO_ROOT", () => {
    expect(lib.resolveDir("../outside", "upload")).toBeNull();
    expect(lib.resolveDir("/etc", "upload")).toBeNull();
  });

  it("rejects dirs not in the root's includeDirs", () => {
    expect(lib.resolveDir(AGENT_ROOT, "secrets")).toBeNull();
    // includeDirs membership is an exact string match — traversal spellings miss it.
    expect(lib.resolveDir(AGENT_ROOT, "../upload")).toBeNull();
    expect(lib.resolveDir(AGENT_ROOT, "upload/../outputs")).toBeNull();
  });
});

describe("extOf", () => {
  it("lowercases the extension", () => {
    expect(lib.extOf("notes.MD")).toBe("md");
    expect(lib.extOf("photo.PNG")).toBe("png");
  });

  it("returns the segment after the final dot", () => {
    expect(lib.extOf("archive.tar.gz")).toBe("gz");
    expect(lib.extOf(".env")).toBe("env");
  });

  it("returns the whole lowercased string when there is no dot", () => {
    // Actual behavior: split(".").pop() of a dotless string is the string itself.
    expect(lib.extOf("README")).toBe("readme");
    expect(lib.extOf("dir/file")).toBe("dir/file");
  });

  it("returns an empty string for a trailing dot", () => {
    expect(lib.extOf("file.")).toBe("");
  });
});

describe("isBinaryPath", () => {
  it("flags known binary extensions, case-insensitively", () => {
    expect(lib.isBinaryPath("a.png")).toBe(true);
    expect(lib.isBinaryPath("a.PDF")).toBe(true);
    expect(lib.isBinaryPath("a.tar.gz")).toBe(true);
  });

  it("treats everything else as text", () => {
    expect(lib.isBinaryPath("a.md")).toBe(false);
    expect(lib.isBinaryPath("a.txt")).toBe(false);
    expect(lib.isBinaryPath("README")).toBe(false);
  });
});

describe("encodingFor", () => {
  it("uses utf-8 for text and base64 for binary", () => {
    expect(lib.encodingFor("a.md")).toBe("utf-8");
    expect(lib.encodingFor("a.json")).toBe("utf-8");
    expect(lib.encodingFor("a.png")).toBe("base64");
    expect(lib.encodingFor("a.docx")).toBe("base64");
  });
});
