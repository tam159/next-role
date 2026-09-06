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

// The chart body is exercised in its own test; here we only assert routing.
vi.mock("@/app/components/charts/InlineChart", () => ({
  InlineChart: ({ path }: { path: string }) => <div data-testid="inline-chart" data-path={path} />,
}));

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

describe("chart embeds", () => {
  const CHART = "![Runs per day](/charts/t-1/runs.plotly.json)";

  it("draws the chart in a report, where the embed is the content", () => {
    render(<MarkdownContent content={CHART} embedCharts />);

    expect(screen.getByTestId("inline-chart")).toHaveAttribute(
      "data-path",
      "/charts/t-1/runs.plotly.json"
    );
  });

  it("does not wrap an embedded chart in a paragraph", () => {
    // Plotly draws block elements, which are invalid inside <p> and trip
    // React's hydration check.
    const { container } = render(<MarkdownContent content={CHART} embedCharts />);

    expect(container.querySelector("p [data-testid='inline-chart']")).toBeNull();
    expect(container.querySelector("[data-testid='inline-chart']")).not.toBeNull();
  });

  it("links rather than redraws in chat, where the card already showed it", () => {
    // The chart tool call renders its own card above the reply; drawing the
    // same figure again from the prose showed it twice.
    resolveFile.mockReturnValue("/charts/t-1/runs.plotly.json");

    render(<MarkdownContent content={CHART} />);

    expect(screen.queryByTestId("inline-chart")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Runs per day/ })).toBeInTheDocument();
  });

  it("opens the chart from that link", async () => {
    resolveFile.mockReturnValue("/charts/t-1/runs.plotly.json");
    render(<MarkdownContent content={CHART} />);

    await userEvent.click(screen.getByRole("button", { name: /Runs per day/ }));

    expect(openFile).toHaveBeenCalledWith("/charts/t-1/runs.plotly.json");
  });

  it("falls back to the caption when the chart file is unknown", () => {
    // Same rule as file-path links: never link a path that does not resolve.
    resolveFile.mockReturnValue(null);

    render(<MarkdownContent content={CHART} />);

    expect(screen.getByText("Runs per day")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("leaves an ordinary image alone", () => {
    render(<MarkdownContent content="![A photo](https://example.com/a.png)" />);

    expect(screen.getByRole("img")).toHaveAttribute("src", "https://example.com/a.png");
    expect(screen.queryByTestId("inline-chart")).not.toBeInTheDocument();
  });
});
