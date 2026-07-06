import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MarkdownContent } from "@/app/components/MarkdownContent";

// Real react-markdown / remark-gfm / remarkFilePaths run under vitest; only the
// file-preview context is mocked so resolveFile/openFile are controllable.
const { resolveFile, openFile } = vi.hoisted(() => ({
  resolveFile: vi.fn<(candidate: string) => string | null>(),
  openFile: vi.fn<(key: string) => void>(),
}));

vi.mock("@/providers/FilePreviewProvider", () => ({
  useFilePreview: () => ({ resolveFile, openFile }),
}));

beforeEach(() => {
  resolveFile.mockReset().mockReturnValue(null);
  openFile.mockReset();
});

describe("MarkdownContent", () => {
  it("renders GFM tables as real <table> markup", () => {
    render(<MarkdownContent content={"| Col A | Col B |\n| --- | --- |\n| 1 | 2 |"} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Col A" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "2" })).toBeInTheDocument();
  });

  it("turns a resolvable bare file path into a file-link button that opens the file", async () => {
    const user = userEvent.setup();
    resolveFile.mockImplementation((candidate) =>
      candidate === "/processed/cv.md" ? "processed/cv.md" : null
    );
    const { container } = render(
      <MarkdownContent content="See /processed/cv.md for the parsed resume." />
    );

    const fileLink = screen.getByRole("button", { name: "/processed/cv.md" });
    expect(fileLink).toHaveAttribute("title", "Open file");
    // The sentinel scheme never reaches the DOM as an anchor.
    expect(container.querySelector("a")).toBeNull();

    await user.click(fileLink);
    expect(openFile).toHaveBeenCalledTimes(1);
    expect(openFile).toHaveBeenCalledWith("processed/cv.md");
  });

  it("renders an unresolvable path as plain text with no anchor and no leaked scheme", () => {
    const { container } = render(<MarkdownContent content="See /procesed/cv.md for details." />);

    expect(container.textContent).toContain("See /procesed/cv.md for details.");
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector('[href*="nextrole-file"]')).toBeNull();
    expect(screen.queryByTitle("Open file")).not.toBeInTheDocument();
    expect(openFile).not.toHaveBeenCalled();
  });

  it("renders regular https links as new-tab anchors with rel protection", () => {
    render(<MarkdownContent content="[Example](https://example.com/jobs)" />);

    const link = screen.getByRole("link", { name: "Example" });
    expect(link).toHaveAttribute("href", "https://example.com/jobs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("turns inline code naming a resolvable file into a file link", async () => {
    const user = userEvent.setup();
    resolveFile.mockImplementation((candidate) =>
      candidate === "/processed/cv.md" ? "processed/cv.md" : null
    );
    render(<MarkdownContent content={"Open `/processed/cv.md` to review."} />);

    const fileLink = screen.getByRole("button", { name: "/processed/cv.md" });
    expect(fileLink).toHaveAttribute("title", "Open file");

    await user.click(fileLink);
    expect(openFile).toHaveBeenCalledWith("processed/cv.md");
  });

  it("renders inline code that does not resolve as a plain <code> element", () => {
    const { container } = render(<MarkdownContent content={"Run `pnpm dev` locally."} />);

    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code).toHaveTextContent("pnpm dev");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders fenced code blocks through the syntax highlighter", () => {
    const { container } = render(<MarkdownContent content={"```js\nconst x = 1;\n```"} />);

    const highlighted = container.querySelector("code.language-js");
    expect(highlighted).not.toBeNull();
    expect(highlighted!.textContent).toContain("const x = 1;");
  });
});
