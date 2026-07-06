import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import { PRINT_FILE_STORAGE_KEY, parsePayload } from "@/app/print/file/printPayload";
import type { FileItem } from "@/app/types/types";
import { FileViewDialog } from "./FileViewDialog";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// The component loads mammoth via `await import("mammoth")`; vi.mock also
// intercepts dynamic imports, so this factory is what that await resolves to.
const convertToHtml = vi.hoisted(() =>
  vi.fn(async (_input: { arrayBuffer: ArrayBuffer }) => ({ value: "<p>docx html</p>" }))
);
vi.mock("mammoth", () => ({ convertToHtml }));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

vi.mock("@/app/components/MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

// react-syntax-highlighter drags in a large grammar bundle; the dialog only
// needs "a code renderer" for non-markdown text files.
vi.mock("react-syntax-highlighter", () => ({
  Prism: ({ children }: { children: string }) => (
    <pre data-testid="syntax-highlighter">{children}</pre>
  ),
}));
vi.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({ oneDark: {} }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderDialog(file: FileItem | null, overrides: { editDisabled?: boolean } = {}) {
  const onSaveFile = vi.fn(async () => {});
  const onDelete = vi.fn();
  const onClose = vi.fn();
  render(
    // Fresh SWR cache per test — useSWRMutation drives the save flow.
    <SWRConfig value={{ provider: () => new Map() }}>
      <FileViewDialog
        file={file}
        onSaveFile={onSaveFile}
        onDelete={onDelete}
        onClose={onClose}
        editDisabled={overrides.editDisabled ?? false}
      />
    </SWRConfig>
  );
  return { onSaveFile, onDelete, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  sessionStorage.clear();
  document.title = "";
  // The print flow appends its iframe to document.body, outside the RTL
  // container — RTL cleanup won't remove it.
  document.querySelectorAll("iframe").forEach((el) => el.remove());
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FileViewDialog", () => {
  it("renders markdown files through MarkdownContent", () => {
    renderDialog({ path: "research/notes.md", content: "# Hello" });

    expect(screen.getByTestId("markdown-content")).toHaveTextContent("# Hello");
    // Path appears in both the sr-only dialog title and the visible header.
    expect(screen.getAllByText("research/notes.md").length).toBeGreaterThan(0);
  });

  it("renders non-markdown text files through the syntax highlighter", () => {
    renderDialog({ path: "scripts/tool.py", content: "print(1)" });

    expect(screen.getByTestId("syntax-highlighter")).toHaveTextContent("print(1)");
  });

  it("saves edited content through onSaveFile and leaves edit mode", async () => {
    const user = userEvent.setup();
    const { onSaveFile } = renderDialog({ path: "notes.md", content: "# Hello" });

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const textarea = screen.getByPlaceholderText("Enter file content...");
    expect(textarea).toHaveValue("# Hello");

    const saveButton = screen.getByRole("button", { name: "Save" });
    await user.clear(textarea);
    expect(saveButton).toBeDisabled();

    await user.type(textarea, "updated body");
    expect(saveButton).toBeEnabled();
    await user.click(saveButton);

    await waitFor(() => expect(onSaveFile).toHaveBeenCalledWith("notes.md", "updated body"));
    expect(onSaveFile).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.queryByPlaceholderText("Enter file content...")).not.toBeInTheDocument()
    );
  });

  it("renders image files as a base64 data-URL <img> and disables editing", () => {
    renderDialog({ path: "upload/pic.png", content: "iVBORw0KGgoAAA" });

    const img = screen.getByAltText("upload/pic.png");
    const src = img.getAttribute("src") ?? "";
    expect(src.startsWith("data:image/png;base64,")).toBe(true);
    expect(src).toBe("data:image/png;base64,iVBORw0KGgoAAA");
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Print" })).not.toBeInTheDocument();
  });

  it("decodes docx base64 into the exact bytes for mammoth and renders the HTML", async () => {
    const bytes = [0x50, 0x4b, 0x03, 0x04, 0x00, 0xff, 0x10, 0x80];
    const b64 = btoa(String.fromCharCode(...bytes));
    renderDialog({ path: "upload/resume.docx", content: b64 });

    expect(await screen.findByText("docx html")).toBeInTheDocument();
    expect(convertToHtml).toHaveBeenCalledTimes(1);
    const arg = convertToHtml.mock.calls[0][0];
    expect(arg.arrayBuffer).toBeInstanceOf(ArrayBuffer);
    expect(Array.from(new Uint8Array(arg.arrayBuffer))).toEqual(bytes);
  });

  it("stores a print payload and opens a hidden /print/file iframe on Print", async () => {
    const user = userEvent.setup();
    renderDialog({ path: "research/acme/notes.md", content: "# Hello\n\nBody." });

    await user.click(screen.getByRole("button", { name: "Print" }));

    const payload = parsePayload(sessionStorage.getItem(PRINT_FILE_STORAGE_KEY));
    expect(payload).toEqual({
      path: "research/acme/notes.md",
      kind: "markdown",
      content: "# Hello\n\nBody.",
    });

    const iframe = document.querySelector('iframe[src="/print/file"]');
    expect(iframe).not.toBeNull();
    expect(iframe?.getAttribute("aria-hidden")).toBe("true");
    // Top-level title becomes the suggested print filename (slashes → dashes,
    // extension stripped). Deliberately not awaiting the iframe load — jsdom
    // never navigates it.
    expect(document.title).toBe("research-acme-notes");
  });

  it("prints docx files using the converted HTML as the payload", async () => {
    const user = userEvent.setup();
    const b64 = btoa(String.fromCharCode(1, 2, 3));
    renderDialog({ path: "upload/resume.docx", content: b64 });
    await screen.findByText("docx html");

    await user.click(screen.getByRole("button", { name: "Print" }));

    const payload = parsePayload(sessionStorage.getItem(PRINT_FILE_STORAGE_KEY));
    expect(payload).toEqual({
      path: "upload/resume.docx",
      kind: "docx",
      content: "<p>docx html</p>",
    });
  });

  it("downloads text files through a temporary object URL", async () => {
    const user = userEvent.setup();
    // jsdom has no URL.createObjectURL; install a cheap stub and restore after.
    const urlStatics = URL as unknown as Record<string, unknown>;
    const originalCreate = urlStatics.createObjectURL;
    const originalRevoke = urlStatics.revokeObjectURL;
    const createObjectURL = vi.fn((_blob: Blob) => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    urlStatics.createObjectURL = createObjectURL;
    urlStatics.revokeObjectURL = revokeObjectURL;
    let downloadName = "";
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloadName = this.download;
    });

    try {
      renderDialog({ path: "research/notes.md", content: "# Hello" });
      await user.click(screen.getByRole("button", { name: "Download" }));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(downloadName).toBe("notes.md");
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    } finally {
      clickSpy.mockRestore();
      if (originalCreate === undefined) delete urlStatics.createObjectURL;
      else urlStatics.createObjectURL = originalCreate;
      if (originalRevoke === undefined) delete urlStatics.revokeObjectURL;
      else urlStatics.revokeObjectURL = originalRevoke;
    }
  });

  it("renders pdf files in an embedded data-URL iframe without a Print action", () => {
    renderDialog({ path: "upload/cv.pdf", content: "JVBERi0=" });

    const frame = screen.getByTitle("upload/cv.pdf");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("src")).toBe("data:application/pdf;base64,JVBERi0=");
    expect(screen.queryByRole("button", { name: "Print" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  });

  it("shows the empty-file placeholder", () => {
    renderDialog({ path: "empty.md", content: "" });

    expect(screen.getByText("File is empty")).toBeInTheDocument();
  });
});
