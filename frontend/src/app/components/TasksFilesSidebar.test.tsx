import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TodoItem } from "@/app/types/types";
import { FilesPopover, TasksFilesSidebar } from "./TasksFilesSidebar";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
vi.mock("sonner", () => ({ toast, Toaster: () => null }));

const removeFile = vi.fn(async (_path: string) => {});
const removeFiles = vi.fn(async (paths: string[]) => ({
  deleted: paths,
  errors: [] as { path: string; reason: string }[],
}));
const chatCtx: {
  isLoading: boolean;
  interrupt: unknown;
  removeFile: typeof removeFile;
  removeFiles: typeof removeFiles;
} = { isLoading: false, interrupt: undefined, removeFile, removeFiles };

vi.mock("@/providers/ChatProvider", () => ({
  useChatContext: () => chatCtx,
}));

// Stub the (heavy, Radix-dialog) file viewer; capture its props so open-file
// and save wiring can be asserted.
let dialogProps: {
  file: { path: string; content: string } | null;
  onSaveFile: (name: string, content: string) => Promise<void>;
  onDelete?: (name: string) => void;
  onClose: () => void;
  editDisabled: boolean;
} | null = null;
vi.mock("@/app/components/FileViewDialog", () => ({
  FileViewDialog: (props: NonNullable<typeof dialogProps>) => {
    dialogProps = props;
    return <div data-testid="file-view-dialog">{props.file?.path}</div>;
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const PATH_RESUME = "tailored_resume/acme/resume.md";
const PATH_RESEARCH = "research/acme/company.md";
const PATH_UPLOAD = "upload/cv.pdf";
const PATH_LOOSE = "scratch/notes.txt"; // no matching FILE_CATEGORIES prefix

const FILES: Record<string, string> = {
  [PATH_RESUME]: "resume body",
  [PATH_RESEARCH]: "research body",
  [PATH_UPLOAD]: "JVBERi0=",
  [PATH_LOOSE]: "loose note",
};

const setFiles = vi.fn(async () => {});

function renderSidebar(overrides: { todos?: TodoItem[]; files?: Record<string, string> } = {}) {
  return render(
    <TasksFilesSidebar
      todos={overrides.todos ?? []}
      files={overrides.files ?? FILES}
      setFiles={setFiles}
    />
  );
}

type User = ReturnType<typeof userEvent.setup>;

async function openFilesPanel(user: User) {
  await user.click(screen.getByRole("button", { name: "Toggle files panel" }));
}

async function selectTwoAndConfirmDelete(user: User) {
  await openFilesPanel(user);
  await user.click(screen.getByRole("checkbox", { name: `Select ${PATH_RESUME}` }));
  await user.click(screen.getByRole("checkbox", { name: `Select ${PATH_RESEARCH}` }));
  // Toolbar Delete (the per-card buttons are named "Delete <path>").
  await user.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Delete 2 files?")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  chatCtx.isLoading = false;
  chatCtx.interrupt = undefined;
  dialogProps = null;
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TasksFilesSidebar", () => {
  // NOTE: files are NOT grouped under category headings — the grid is flat.
  // getFileCategory only drives the per-card color; unknown top-level dirs
  // fall back to the neutral text color.
  it("renders every file as a card, color-bucketed by path category", async () => {
    const user = userEvent.setup();
    renderSidebar();

    // Files panel starts collapsed when files exist at mount.
    expect(screen.queryByTitle(PATH_RESUME)).not.toBeInTheDocument();
    await openFilesPanel(user);

    for (const path of Object.keys(FILES)) {
      expect(screen.getByTitle(path)).toBeInTheDocument();
    }

    const resumeCard = screen.getByTitle(PATH_RESUME);
    expect(within(resumeCard).getByText("resume")).toBeInTheDocument();
    expect(within(resumeCard).getByText("tailored_resume/acme/")).toBeInTheDocument();
    expect(within(resumeCard).getByText("MD")).toHaveStyle({ color: "var(--color-primary)" });

    const researchCard = screen.getByTitle(PATH_RESEARCH);
    expect(within(researchCard).getByText("MD")).toHaveStyle({
      color: "var(--color-category-slate)",
    });

    const uploadCard = screen.getByTitle(PATH_UPLOAD);
    expect(within(uploadCard).getByText("PDF")).toHaveStyle({
      color: "var(--color-category-rose)",
    });

    const looseCard = screen.getByTitle(PATH_LOOSE);
    expect(within(looseCard).getByText("TXT")).toHaveStyle({ color: "var(--text-secondary)" });
  });

  it("auto-expands the files panel when files first appear", () => {
    const { rerender } = renderSidebar({ files: {} });
    expect(screen.queryByTitle(PATH_UPLOAD)).not.toBeInTheDocument();

    rerender(<TasksFilesSidebar todos={[]} files={FILES} setFiles={setFiles} />);

    expect(screen.getByTitle(PATH_UPLOAD)).toBeInTheDocument();
  });

  it("shows the files empty state", async () => {
    const user = userEvent.setup();
    renderSidebar({ files: {} });
    await openFilesPanel(user);

    expect(screen.getByText("No files created yet")).toBeInTheDocument();
  });

  it("opens the FileViewDialog for a clicked file and wires saving back to setFiles", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await openFilesPanel(user);

    await user.click(screen.getByTitle(PATH_RESEARCH));

    expect(screen.getByTestId("file-view-dialog")).toHaveTextContent(PATH_RESEARCH);
    expect(dialogProps?.file).toEqual({ path: PATH_RESEARCH, content: "research body" });
    expect(dialogProps?.editDisabled).toBe(false);

    await act(async () => {
      await dialogProps?.onSaveFile(PATH_RESEARCH, "updated body");
    });
    expect(setFiles).toHaveBeenCalledWith({ ...FILES, [PATH_RESEARCH]: "updated body" });
    expect(dialogProps?.file).toEqual({ path: PATH_RESEARCH, content: "updated body" });
  });

  it("deletes the selected files after confirmation and clears the selection", async () => {
    const user = userEvent.setup();
    renderSidebar();

    await selectTwoAndConfirmDelete(user);

    await waitFor(() => expect(removeFiles).toHaveBeenCalledWith([PATH_RESUME, PATH_RESEARCH]));
    expect(removeFiles).toHaveBeenCalledTimes(1);
    expect(removeFile).not.toHaveBeenCalled();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Deleted 2 files"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Selection toolbar gone once nothing is selected.
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();
  });

  it("shows an error toast and keeps failed paths selected when every delete fails", async () => {
    const user = userEvent.setup();
    removeFiles.mockResolvedValueOnce({
      deleted: [],
      errors: [
        { path: PATH_RESUME, reason: "locked" },
        { path: PATH_RESEARCH, reason: "locked" },
      ],
    });
    renderSidebar();

    await selectTwoAndConfirmDelete(user);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Failed to delete 2 files"));
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Failed paths stay selected so the user can see what's left.
    expect(screen.getByRole("checkbox", { name: `Deselect ${PATH_RESUME}` })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Deselect ${PATH_RESEARCH}` })).toBeInTheDocument();
  });

  it("shows a warning toast on partial failure and keeps only failed paths selected", async () => {
    const user = userEvent.setup();
    removeFiles.mockResolvedValueOnce({
      deleted: [PATH_RESUME],
      errors: [{ path: PATH_RESEARCH, reason: "in use" }],
    });
    renderSidebar();

    await selectTwoAndConfirmDelete(user);

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith("Deleted 1 of 2 (1 failed)"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: `Deselect ${PATH_RESEARCH}` })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Select ${PATH_RESUME}` })).toBeInTheDocument();
  });

  it("deletes a single file from its card through removeFile", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await openFilesPanel(user);

    await user.click(screen.getByRole("button", { name: `Delete ${PATH_UPLOAD}` }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete file?")).toBeInTheDocument();
    expect(within(dialog).getByText("cv.pdf")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(removeFile).toHaveBeenCalledWith(PATH_UPLOAD));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Deleted cv.pdf"));
    expect(removeFiles).not.toHaveBeenCalled();
  });

  it("disables destructive actions while the agent is streaming", async () => {
    const user = userEvent.setup();
    chatCtx.isLoading = true;
    renderSidebar();
    await openFilesPanel(user);

    expect(screen.getByRole("button", { name: `Delete ${PATH_UPLOAD}` })).toBeDisabled();
  });

  it("groups tasks under status headings", async () => {
    const user = userEvent.setup();
    // The todo-group markup renders a keyless list; silence React's key
    // warning for this test only.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderSidebar({
        todos: [
          { id: "1", content: "Draft resume", status: "pending" },
          { id: "2", content: "Research Acme", status: "in_progress" },
          { id: "3", content: "Send follow-up", status: "completed" },
        ],
      });
      await user.click(screen.getByRole("button", { name: "Toggle tasks panel" }));

      expect(screen.getByRole("heading", { name: "Pending", level: 3 })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "In Progress", level: 3 })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Completed", level: 3 })).toBeInTheDocument();
      expect(screen.getByText("Draft resume")).toBeInTheDocument();
      expect(screen.getByText("Research Acme")).toBeInTheDocument();
      expect(screen.getByText("Send follow-up")).toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});

// ---------------------------------------------------------------------------
// FilesPopover upload-files tile (persistent upload affordance in the grid)
// ---------------------------------------------------------------------------

describe("FilesPopover upload-files tile", () => {
  const baseProps = {
    files: { [PATH_UPLOAD]: "cv body" },
    setFiles: vi.fn(async () => {}),
    removeFile,
    removeFiles,
    editDisabled: false,
  };

  it("renders the tile alongside file cards and forwards clicks", async () => {
    const onAddFiles = vi.fn();
    const user = userEvent.setup();
    render(<FilesPopover {...baseProps} onAddFiles={onAddFiles} />);

    const tile = screen.getByRole("button", { name: /upload files/i });
    await user.click(tile);
    expect(onAddFiles).toHaveBeenCalledTimes(1);
  });

  it("omits the tile when onAddFiles is not provided", () => {
    render(<FilesPopover {...baseProps} />);
    expect(screen.queryByRole("button", { name: /upload files/i })).not.toBeInTheDocument();
  });

  it("disables the tile and shows progress while uploading", () => {
    render(<FilesPopover {...baseProps} onAddFiles={vi.fn()} uploading />);
    expect(screen.getByRole("button", { name: /uploading/i })).toBeDisabled();
  });

  it("disables the tile while the agent is streaming (editDisabled)", () => {
    render(<FilesPopover {...baseProps} onAddFiles={vi.fn()} editDisabled />);
    expect(screen.getByRole("button", { name: /upload files/i })).toBeDisabled();
  });

  it("uploads accepted files dropped on the tile and reports skipped ones", async () => {
    const onDropFiles = vi.fn();
    render(<FilesPopover {...baseProps} onAddFiles={vi.fn()} onDropFiles={onDropFiles} />);

    const pdf = new File(["x"], "jd.pdf", { type: "application/pdf" });
    const exe = new File(["x"], "setup.exe", { type: "application/octet-stream" });
    fireEvent.drop(screen.getByRole("button", { name: /upload files/i }), {
      dataTransfer: { files: [pdf, exe] },
    });

    await waitFor(() => expect(onDropFiles).toHaveBeenCalledWith([pdf]));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Skipped 1 unsupported file"));
  });
});
