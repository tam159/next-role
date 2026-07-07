import type { ComponentProps } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { ThreadList, formatTime } from "@/app/components/ThreadList";
import { useThreads } from "@/app/hooks/useThreads";
import type { ThreadItem } from "@/app/hooks/useThreads";

vi.mock("@/app/hooks/useThreads", () => ({ useThreads: vi.fn() }));

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

function threadItem(id: string, overrides: Partial<ThreadItem> = {}): ThreadItem {
  return {
    id,
    updatedAt: new Date(Date.now() - HOUR),
    status: "idle",
    title: `Thread ${id}`,
    description: `About ${id}`,
    ...overrides,
  };
}

function swrState(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    error: undefined,
    isLoading: false,
    isValidating: false,
    size: 1,
    setSize: vi.fn(),
    mutate: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useThreads>;
}

function renderList(
  props: Partial<ComponentProps<typeof ThreadList>> = {},
  searchParams: string = ""
) {
  const onThreadSelect = props.onThreadSelect ?? vi.fn();
  const utils = render(
    <NuqsTestingAdapter searchParams={searchParams}>
      <ThreadList onThreadSelect={onThreadSelect} {...props} />
    </NuqsTestingAdapter>
  );
  return { ...utils, onThreadSelect };
}

beforeEach(() => {
  vi.mocked(useThreads).mockReturnValue(swrState());
});

describe("ThreadList", () => {
  it("shows loading skeletons while the first page loads", () => {
    vi.mocked(useThreads).mockReturnValue(swrState({ isLoading: true }));
    const { container } = renderList();

    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(5);
  });

  it("shows the error state with the error message", () => {
    vi.mocked(useThreads).mockReturnValue(swrState({ error: new Error("boom") }));
    renderList();

    expect(screen.getByText("Failed to load threads")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("shows the empty state when the first page has no threads", () => {
    vi.mocked(useThreads).mockReturnValue(swrState({ data: [[]] }));
    renderList();

    expect(screen.getByText("No threads found")).toBeInTheDocument();
    expect(screen.getByText("New conversations will appear here.")).toBeInTheDocument();
  });

  it("groups threads and renders their titles and descriptions", () => {
    vi.mocked(useThreads).mockReturnValue(
      swrState({
        data: [
          [
            threadItem("today", { title: "Fix resume", description: "Tighten the summary" }),
            threadItem("stuck", { status: "interrupted", title: "Needs input" }),
            threadItem("old", { updatedAt: new Date(Date.now() - 30 * DAY), title: "Old chat" }),
          ],
        ],
      })
    );
    const onInterruptCountChange = vi.fn();
    renderList({ onInterruptCountChange });

    const groupFor = (label: string) =>
      screen.getByRole("heading", { name: label }).parentElement as HTMLElement;
    expect(within(groupFor("Today")).getByText("Fix resume")).toBeInTheDocument();
    expect(within(groupFor("Today")).getByText("Tighten the summary")).toBeInTheDocument();
    expect(within(groupFor("Requiring Attention")).getByText("Needs input")).toBeInTheDocument();
    expect(within(groupFor("Older")).getByText("Old chat")).toBeInTheDocument();
    expect(onInterruptCountChange).toHaveBeenCalledWith(1);
  });

  it("passes the selected status filter to useThreads (server-side filtering)", async () => {
    const user = userEvent.setup();
    vi.mocked(useThreads).mockReturnValue(swrState({ data: [[threadItem("t-1")]] }));
    renderList();

    expect(vi.mocked(useThreads)).toHaveBeenCalledWith({ status: undefined, limit: 20 });

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Error" }));

    expect(vi.mocked(useThreads)).toHaveBeenLastCalledWith({ status: "error", limit: 20 });
  });

  it("invokes onThreadSelect with the thread id when a thread is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(useThreads).mockReturnValue(
      swrState({ data: [[threadItem("t-1"), threadItem("t-2")]] })
    );
    const { onThreadSelect } = renderList();

    await user.click(screen.getByRole("button", { name: /Thread t-2/ }));

    expect(onThreadSelect).toHaveBeenCalledTimes(1);
    expect(onThreadSelect).toHaveBeenCalledWith("t-2");
  });

  it("marks the thread from the threadId URL param as current", () => {
    vi.mocked(useThreads).mockReturnValue(
      swrState({ data: [[threadItem("t-1"), threadItem("t-2")]] })
    );
    renderList({}, "?threadId=t-1");

    expect(screen.getByRole("button", { name: /Thread t-1/ })).toHaveAttribute(
      "aria-current",
      "true"
    );
    expect(screen.getByRole("button", { name: /Thread t-2/ })).toHaveAttribute(
      "aria-current",
      "false"
    );
  });

  it("loads the next page when Load More is clicked", async () => {
    const user = userEvent.setup();
    const setSize = vi.fn();
    const fullPage = Array.from({ length: 20 }, (_, i) => threadItem(`t-${i}`));
    vi.mocked(useThreads).mockReturnValue(swrState({ data: [fullPage], size: 1, setSize }));
    renderList();

    await user.click(screen.getByRole("button", { name: "Load More" }));

    expect(setSize).toHaveBeenCalledWith(2);
  });
});

describe("formatTime", () => {
  // Monday 2026-06-15, mid-day to stay clear of timezone/day edges.
  const now = new Date(2026, 5, 15, 12, 0, 0);

  it("formats same-day timestamps as HH:mm", () => {
    expect(formatTime(new Date(2026, 5, 15, 9, 5), now)).toBe("09:05");
  });

  it('formats one-day-old timestamps as "Yesterday"', () => {
    expect(formatTime(new Date(2026, 5, 14, 11, 0), now)).toBe("Yesterday");
  });

  it("formats timestamps within the week as the weekday name", () => {
    expect(formatTime(new Date(2026, 5, 12, 12, 0), now)).toBe("Friday");
  });

  it("formats older timestamps as MM/dd", () => {
    expect(formatTime(new Date(2026, 5, 1, 12, 0), now)).toBe("06/01");
  });
});
