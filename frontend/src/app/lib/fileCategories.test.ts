import { FILE_CATEGORIES, getFileCategory, splitBasename, splitFilePath } from "./fileCategories";

describe("getFileCategory", () => {
  it.each(FILE_CATEGORIES.map((c) => [c.prefix, c.label]))(
    "maps %s/… to the %s category",
    (prefix, label) => {
      expect(getFileCategory(`${prefix}/anything.md`)?.label).toBe(label);
    }
  );

  it("strips any number of leading slashes", () => {
    expect(getFileCategory("/upload/cv.pdf")?.label).toBe("Upload");
    expect(getFileCategory("///upload/cv.pdf")?.label).toBe("Upload");
  });

  it("matches a bare prefix with no trailing path", () => {
    expect(getFileCategory("research")?.label).toBe("Research");
  });

  it("requires an exact first segment, not a prefix match", () => {
    expect(getFileCategory("uploads/cv.pdf")).toBeNull();
    expect(getFileCategory("upload_extra/cv.pdf")).toBeNull();
  });

  it("returns null for unknown categories and empty paths", () => {
    expect(getFileCategory("unknown/file.txt")).toBeNull();
    expect(getFileCategory("")).toBeNull();
    expect(getFileCategory("///")).toBeNull();
  });
});

describe("splitFilePath", () => {
  it("splits directory prefix and basename", () => {
    expect(splitFilePath("a/b/c.md")).toEqual({ prefix: "a/b/", basename: "c.md" });
  });

  it("returns an empty prefix when there is no slash", () => {
    expect(splitFilePath("c.md")).toEqual({ prefix: "", basename: "c.md" });
  });

  it("returns an empty basename for a trailing slash", () => {
    expect(splitFilePath("a/b/")).toEqual({ prefix: "a/b/", basename: "" });
  });
});

describe("splitBasename", () => {
  it("splits stem and extension at the last dot", () => {
    expect(splitBasename("report.final.md")).toEqual({ stem: "report.final", ext: ".md" });
  });

  it("keeps dotfiles intact", () => {
    expect(splitBasename(".env")).toEqual({ stem: ".env", ext: "" });
  });

  it("treats a trailing dot as part of the stem", () => {
    expect(splitBasename("name.")).toEqual({ stem: "name.", ext: "" });
  });

  it("returns an empty extension when there is no dot", () => {
    expect(splitBasename("README")).toEqual({ stem: "README", ext: "" });
  });
});
