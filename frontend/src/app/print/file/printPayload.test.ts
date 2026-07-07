import { basenameWithoutExtension, parsePayload, type PrintPayload } from "./printPayload";

describe("parsePayload", () => {
  it("parses a valid markdown payload", () => {
    const payload: PrintPayload = { path: "resume.md", content: "# Resume", kind: "markdown" };
    expect(parsePayload(JSON.stringify(payload))).toEqual(payload);
  });

  it("parses a valid code payload with a language", () => {
    const payload: PrintPayload = {
      path: "scripts/build.ts",
      content: "console.log(1);",
      kind: "code",
      language: "typescript",
    };
    expect(parsePayload(JSON.stringify(payload))).toEqual(payload);
  });

  it("parses a valid docx payload", () => {
    const payload: PrintPayload = { path: "letter.docx", content: "<p>Hi</p>", kind: "docx" };
    expect(parsePayload(JSON.stringify(payload))).toEqual(payload);
  });

  it("rejects an unknown kind", () => {
    expect(parsePayload(JSON.stringify({ path: "a.pdf", content: "x", kind: "pdf" }))).toBeNull();
  });

  it("rejects payloads with a missing or non-string path or content", () => {
    expect(parsePayload(JSON.stringify({ content: "x", kind: "markdown" }))).toBeNull();
    expect(parsePayload(JSON.stringify({ path: "a.md", kind: "markdown" }))).toBeNull();
    expect(parsePayload(JSON.stringify({ path: 42, content: "x", kind: "markdown" }))).toBeNull();
  });

  it("returns null for non-JSON input", () => {
    expect(parsePayload("not json")).toBeNull();
  });

  it("returns null for null or empty input", () => {
    expect(parsePayload(null)).toBeNull();
    expect(parsePayload("")).toBeNull();
  });
});

describe("basenameWithoutExtension", () => {
  it("strips directories and the extension from a nested path", () => {
    expect(basenameWithoutExtension("career/resume/final.md")).toBe("final");
  });

  it("leaves names without an extension unchanged", () => {
    expect(basenameWithoutExtension("README")).toBe("README");
    expect(basenameWithoutExtension("docs/README")).toBe("README");
  });

  it("strips only the last extension when the name has multiple dots", () => {
    expect(basenameWithoutExtension("backup.tar.gz")).toBe("backup.tar");
  });

  it("falls back to the full path for a trailing slash", () => {
    expect(basenameWithoutExtension("folder/sub/")).toBe("folder/sub/");
  });
});
