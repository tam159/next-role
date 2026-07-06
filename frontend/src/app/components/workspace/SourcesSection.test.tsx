import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesSection } from "@/app/components/workspace/SourcesSection";
import type { Source } from "@/app/types/types";

function source(id: string, title: string, url: string): Source {
  return { id, title, url, toolCallId: `tool-${id}` };
}

function renderSources(sources: Source[], props: { open?: boolean; onToggle?: () => void } = {}) {
  return render(
    <SourcesSection
      sources={sources}
      open={props.open ?? true}
      onToggle={props.onToggle ?? vi.fn()}
    />
  );
}

describe("SourcesSection", () => {
  it("derives the host label from the URL and strips the www. prefix", () => {
    renderSources([source("1", "Acme Careers", "https://www.example.com/careers/123")]);

    expect(screen.getByText("Acme Careers")).toBeInTheDocument();
    expect(screen.getByText("example.com")).toBeInTheDocument();
    expect(screen.queryByText(/www\./)).not.toBeInTheDocument();
  });

  it('uses the special "in" avatar letter for linkedin hosts', () => {
    renderSources([source("1", "Jane Doe", "https://www.linkedin.com/in/janedoe")]);

    const item = screen.getByRole("listitem");
    expect(within(item).getByText("in")).toBeInTheDocument();
    expect(within(item).getByText("linkedin.com")).toBeInTheDocument();
  });

  it("falls back to the uppercased first alphanumeric character for other hosts", () => {
    renderSources([source("1", "Acme", "https://example.com/jobs")]);

    expect(screen.getByText("E")).toBeInTheDocument();
  });

  it("renders each source as a new-tab link with rel protection", () => {
    renderSources([source("1", "Acme Careers", "https://www.example.com/careers")]);

    const link = screen.getByRole("link", { name: /Acme Careers/ });
    expect(link).toHaveAttribute("href", "https://www.example.com/careers");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows the source count badge in the header", () => {
    renderSources([
      source("1", "One", "https://a.com/x"),
      source("2", "Two", "https://b.com/y"),
      source("3", "Three", "https://c.com/z"),
    ]);

    expect(screen.getByText("Sources")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders an empty message (not null) when there are no sources", () => {
    renderSources([]);

    expect(screen.getByText("Sources")).toBeInTheDocument();
    expect(screen.getByText("No sources yet")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // Count badge is hidden when the count is zero.
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("falls back to the raw string as host label when the URL cannot be parsed", () => {
    renderSources([source("1", "Mystery Doc", "not a url")]);

    const item = screen.getByRole("listitem");
    expect(within(item).getByText("not a url")).toBeInTheDocument();
    // letterFor picks the first alphanumeric char of the fallback label.
    expect(within(item).getByText("N")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Mystery Doc/ })).toHaveAttribute("href", "not a url");
  });

  it("hides the list when collapsed and toggles via the header", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    renderSources([source("1", "Acme", "https://example.com")], { open: false, onToggle });

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Sources/ }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
