import { act, render, renderHook, screen } from "@testing-library/react";
import { FileViewDialog } from "@/app/components/FileViewDialog";
import { useChatContext } from "@/providers/ChatProvider";
import { FilePreviewProvider, useFilePreview } from "@/providers/FilePreviewProvider";

vi.mock("@/providers/ChatProvider", () => ({ useChatContext: vi.fn() }));
vi.mock("@/app/components/FileViewDialog", () => ({
  FileViewDialog: vi.fn(() => <div data-testid="file-view-dialog" />),
}));

const useChatContextMock = vi.mocked(useChatContext);
const dialogMock = vi.mocked(FileViewDialog);

type PreviewApi = NonNullable<ReturnType<typeof useFilePreview>>;
type ChatCtx = ReturnType<typeof useChatContext>;

function makeChatCtx(overrides: Record<string, unknown> = {}) {
  return {
    files: {
      "/processed/cv.md": "# CV body",
      "notes/summary.md": "summary text",
      "/raw/blocks.md": { content: ["line1", "line2"] },
    },
    setFiles: vi.fn().mockResolvedValue(undefined),
    isLoading: false,
    interrupt: undefined,
    ...overrides,
  } as unknown as ChatCtx;
}

function renderPreview(ctx: ChatCtx = makeChatCtx()) {
  useChatContextMock.mockReturnValue(ctx);
  let api: PreviewApi | null = null;
  function Capture() {
    api = useFilePreview();
    return null;
  }
  render(
    <FilePreviewProvider>
      <Capture />
    </FilePreviewProvider>
  );
  if (!api) throw new Error("preview context not captured");
  return { ctx, api: api as PreviewApi };
}

/** openFile defers via setTimeout(0); run it under fake timers wrapped in act. */
function openFileAndFlush(api: PreviewApi, candidate: string) {
  act(() => {
    api.openFile(candidate);
  });
  act(() => {
    vi.runAllTimers();
  });
}

function lastDialogProps() {
  const call = dialogMock.mock.calls.at(-1);
  if (!call) throw new Error("FileViewDialog was never rendered");
  return call[0];
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("resolveFile", () => {
  it("normalizes messy candidate paths to the canonical file key", () => {
    const { api } = renderPreview();
    expect(api.resolveFile("`/processed/cv.md`,")).toBe("/processed/cv.md");
    expect(api.resolveFile("(/processed/cv.md)")).toBe("/processed/cv.md");
    expect(api.resolveFile("processed/cv.md")).toBe("/processed/cv.md");
    expect(api.resolveFile("  /processed/cv.md  ")).toBe("/processed/cv.md");
  });

  it("returns the stored key when it lacks a leading slash", () => {
    const { api } = renderPreview();
    expect(api.resolveFile("/notes/summary.md")).toBe("notes/summary.md");
  });

  it("returns null for paths that resolve to no known file", () => {
    const { api } = renderPreview();
    expect(api.resolveFile("/procesed/cv.md")).toBeNull();
    expect(api.resolveFile("/missing/nope.md")).toBeNull();
  });
});

describe("openFile", () => {
  it("defers opening to the next tick, then shows the dialog with the resolved file", () => {
    vi.useFakeTimers();
    const { api } = renderPreview();

    act(() => {
      api.openFile("`/processed/cv.md`,");
    });
    // Not yet open: the setTimeout(0) hasn't fired.
    expect(screen.queryByTestId("file-view-dialog")).not.toBeInTheDocument();
    expect(dialogMock).not.toHaveBeenCalled();

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId("file-view-dialog")).toBeInTheDocument();
    const props = lastDialogProps();
    expect(props.file).toEqual({ path: "/processed/cv.md", content: "# CV body" });
    expect(props.editDisabled).toBe(false);
  });

  it("joins array content when the file value is an object with content blocks", () => {
    vi.useFakeTimers();
    const { api } = renderPreview();

    openFileAndFlush(api, "/raw/blocks.md");

    expect(lastDialogProps().file).toEqual({ path: "/raw/blocks.md", content: "line1\nline2" });
  });

  it("does not open a dialog for an unresolvable path", () => {
    vi.useFakeTimers();
    const { api } = renderPreview();

    openFileAndFlush(api, "/ghost.md");

    expect(dialogMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-view-dialog")).not.toBeInTheDocument();
  });
});

describe("dialog wiring", () => {
  it("disables editing while the stream is loading", () => {
    vi.useFakeTimers();
    const { api } = renderPreview(makeChatCtx({ isLoading: true }));

    openFileAndFlush(api, "/processed/cv.md");

    expect(lastDialogProps().editDisabled).toBe(true);
  });

  it("disables editing while an interrupt is pending", () => {
    vi.useFakeTimers();
    const { api } = renderPreview(makeChatCtx({ interrupt: { action_requests: [] } }));

    openFileAndFlush(api, "/processed/cv.md");

    expect(lastDialogProps().editDisabled).toBe(true);
  });

  it("saves through the chat context setFiles with the full merged files map", async () => {
    vi.useFakeTimers();
    const { api, ctx } = renderPreview();

    openFileAndFlush(api, "/processed/cv.md");
    const props = lastDialogProps();
    await act(async () => {
      await props.onSaveFile("/processed/cv.md", "updated body");
    });

    expect(ctx.setFiles).toHaveBeenCalledWith({
      "/processed/cv.md": "updated body",
      "notes/summary.md": "summary text",
      "/raw/blocks.md": { content: ["line1", "line2"] },
    });
  });

  it("closes the dialog via onClose", () => {
    vi.useFakeTimers();
    const { api } = renderPreview();

    openFileAndFlush(api, "/processed/cv.md");
    expect(screen.getByTestId("file-view-dialog")).toBeInTheDocument();

    act(() => {
      lastDialogProps().onClose();
    });

    expect(screen.queryByTestId("file-view-dialog")).not.toBeInTheDocument();
  });
});

describe("useFilePreview", () => {
  it("returns null outside the provider instead of throwing", () => {
    const { result } = renderHook(() => useFilePreview());
    expect(result.current).toBeNull();
  });
});
