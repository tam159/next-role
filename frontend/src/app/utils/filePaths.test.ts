import { FILE_PATH_URL_PREFIX, normalizeFilePath, remarkFilePaths } from "./filePaths";

type MdNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
};

const text = (value: string): MdNode => ({ type: "text", value });
const paragraph = (...children: MdNode[]): MdNode => ({ type: "paragraph", children });
const root = (...children: MdNode[]): MdNode => ({ type: "root", children });

/** Expected shape of a link node produced by the plugin for `path`. */
const fileLink = (path: string): MdNode => ({
  type: "link",
  url: `${FILE_PATH_URL_PREFIX}${path}`,
  children: [text(path)],
});

function transform(tree: MdNode): MdNode {
  remarkFilePaths()(tree);
  return tree;
}

describe("normalizeFilePath", () => {
  it("returns an already-clean path unchanged", () => {
    expect(normalizeFilePath("/processed/resume.md")).toBe("/processed/resume.md");
  });

  it("trims surrounding whitespace", () => {
    expect(normalizeFilePath("  /processed/resume.md\t")).toBe("/processed/resume.md");
  });

  it("strips wrapping quotes and backticks", () => {
    expect(normalizeFilePath('"/processed/resume.md"')).toBe("/processed/resume.md");
    expect(normalizeFilePath("'/processed/resume.md'")).toBe("/processed/resume.md");
    expect(normalizeFilePath("`/processed/resume.md`")).toBe("/processed/resume.md");
  });

  it("strips wrapping parens, brackets, and angle brackets", () => {
    expect(normalizeFilePath("(/processed/resume.md)")).toBe("/processed/resume.md");
    expect(normalizeFilePath("[/processed/resume.md]")).toBe("/processed/resume.md");
    expect(normalizeFilePath("</processed/resume.md>")).toBe("/processed/resume.md");
  });

  it("strips trailing punctuation runs", () => {
    expect(normalizeFilePath("/processed/resume.md.")).toBe("/processed/resume.md");
    expect(normalizeFilePath("/processed/resume.md,;")).toBe("/processed/resume.md");
    expect(normalizeFilePath("/processed/resume.md!?")).toBe("/processed/resume.md");
    expect(normalizeFilePath("/processed/resume.md:")).toBe("/processed/resume.md");
  });

  it("adds a leading slash to relative paths", () => {
    expect(normalizeFilePath("processed/resume.md")).toBe("/processed/resume.md");
  });

  it("handles whitespace, wrappers, trailing punctuation, and missing slash together", () => {
    expect(normalizeFilePath(' "processed/resume.md", ')).toBe("/processed/resume.md");
  });

  it("only strips leading wrappers, not leading path characters", () => {
    // The leading strip class does not include `/`, so absolute paths survive.
    expect(normalizeFilePath("(`/a/b.md`)")).toBe("/a/b.md");
  });
});

describe("remarkFilePaths", () => {
  it("splits a text node with a path in the middle into text + link + text", () => {
    const tree = root(paragraph(text("See /processed/resume.md for details")));
    transform(tree);
    expect(tree.children![0].children).toEqual([
      text("See "),
      fileLink("/processed/resume.md"),
      text(" for details"),
    ]);
  });

  it("handles a path at the start of the text", () => {
    const tree = root(paragraph(text("/processed/resume.md is ready")));
    transform(tree);
    expect(tree.children![0].children).toEqual([
      fileLink("/processed/resume.md"),
      text(" is ready"),
    ]);
  });

  it("handles a path at the end of the text without an empty trailing node", () => {
    const tree = root(paragraph(text("Saved to /processed/resume.md")));
    transform(tree);
    expect(tree.children![0].children).toEqual([
      text("Saved to "),
      fileLink("/processed/resume.md"),
    ]);
  });

  it("links two paths in one text node, keeping the text between them", () => {
    const tree = root(paragraph(text("Compare /a/b.md and /c/d.txt now")));
    transform(tree);
    expect(tree.children![0].children).toEqual([
      text("Compare "),
      fileLink("/a/b.md"),
      text(" and "),
      fileLink("/c/d.txt"),
      text(" now"),
    ]);
  });

  it("excludes trailing sentence punctuation from the linked path", () => {
    const tree = root(paragraph(text("Saved to /a/b.md.")));
    transform(tree);
    expect(tree.children![0].children).toEqual([text("Saved to "), fileLink("/a/b.md"), text(".")]);
  });

  it("leaves text without any path unchanged", () => {
    const tree = root(paragraph(text("no file paths here")));
    transform(tree);
    expect(tree.children![0].children).toEqual([text("no file paths here")]);
  });

  it("does not link a single-segment relative path (regex requires a leading slash)", () => {
    const tree = root(paragraph(text("see processed/resume.md please")));
    transform(tree);
    expect(tree.children![0].children).toEqual([text("see processed/resume.md please")]);
  });

  it("does not link a path without a subfolder", () => {
    const tree = root(paragraph(text("see /resume.md please")));
    transform(tree);
    expect(tree.children![0].children).toEqual([text("see /resume.md please")]);
  });

  it("does not descend into existing link nodes", () => {
    const existing: MdNode = {
      type: "link",
      url: "https://example.com",
      children: [text("/processed/resume.md")],
    };
    const tree = root(paragraph(existing));
    transform(tree);
    const child = tree.children![0].children![0];
    expect(child).toBe(existing);
    expect(child.url).toBe("https://example.com");
    expect(child.children).toEqual([text("/processed/resume.md")]);
  });

  it("leaves inlineCode and code nodes untouched", () => {
    const inline: MdNode = { type: "inlineCode", value: "/processed/resume.md" };
    const block: MdNode = { type: "code", value: "/processed/resume.md" };
    const tree = root(paragraph(inline), block);
    transform(tree);
    expect(tree.children![0].children).toEqual([
      { type: "inlineCode", value: "/processed/resume.md" },
    ]);
    expect(tree.children![1]).toEqual({ type: "code", value: "/processed/resume.md" });
  });

  it("descends into non-excluded containers like emphasis", () => {
    const tree = root(paragraph({ type: "emphasis", children: [text("read /a/b.md now")] }));
    transform(tree);
    const emphasis = tree.children![0].children![0];
    expect(emphasis.type).toBe("emphasis");
    expect(emphasis.children).toEqual([text("read "), fileLink("/a/b.md"), text(" now")]);
  });
});
