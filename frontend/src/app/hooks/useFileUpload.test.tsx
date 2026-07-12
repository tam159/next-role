import { act, renderHook } from "@testing-library/react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";
import { toast } from "sonner";
import { ChatContext, type ChatContextType } from "@/providers/ChatProvider";
import { useFileUpload, useUploadDrop } from "@/app/hooks/useFileUpload";
import { uploadAgentFiles, type UploadResponse } from "@/app/lib/uploadFiles";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/app/lib/uploadFiles", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/app/lib/uploadFiles")>();
  return { ...actual, uploadAgentFiles: vi.fn() };
});

const mockUpload = vi.mocked(uploadAgentFiles);

function renderUpload() {
  const refreshFiles = vi.fn(async () => {});
  const appendUploadNote = vi.fn();
  const ctx = { refreshFiles, appendUploadNote } as unknown as ChatContextType;
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ChatContext.Provider value={ctx}>{children}</ChatContext.Provider>
  );
  const hook = renderHook(() => useFileUpload(), { wrapper });
  return { ...hook, refreshFiles, appendUploadNote };
}

const file = (name: string) => new File(["data"], name, { type: "application/pdf" });

describe("useFileUpload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uploads to the /upload dir, toasts, notes basenames, and refreshes", async () => {
    mockUpload.mockResolvedValue({
      uploaded: [{ path: "/upload/cv.pdf", size: 1 }],
      errors: [],
    });
    const { result, refreshFiles, appendUploadNote } = renderUpload();

    const picked = [file("cv.pdf")];
    await act(() => result.current.uploadFiles(picked));

    expect(mockUpload).toHaveBeenCalledWith({ files: picked, targetDir: "/upload" });
    expect(toast.success).toHaveBeenCalledWith("Uploaded 1 file");
    expect(appendUploadNote).toHaveBeenCalledWith(["cv.pdf"]);
    expect(refreshFiles).toHaveBeenCalledTimes(1);
    expect(result.current.uploading).toBe(false);
  });

  it("toasts each per-file error and skips the note when nothing uploaded", async () => {
    mockUpload.mockResolvedValue({
      uploaded: [],
      errors: [{ name: "cv.pdf", reason: "too large" }],
    });
    const { result, appendUploadNote } = renderUpload();

    await act(() => result.current.uploadFiles([file("cv.pdf")]));

    expect(toast.error).toHaveBeenCalledWith("cv.pdf: too large");
    expect(toast.success).not.toHaveBeenCalled();
    expect(appendUploadNote).not.toHaveBeenCalled();
  });

  it("toasts a thrown failure and resets uploading", async () => {
    mockUpload.mockRejectedValue(new Error("boom"));
    const { result } = renderUpload();

    await act(() => result.current.uploadFiles([file("cv.pdf")]));

    expect(toast.error).toHaveBeenCalledWith("boom");
    expect(result.current.uploading).toBe(false);
  });

  it("ignores a second call while one is in flight", async () => {
    let resolve!: (v: UploadResponse) => void;
    mockUpload.mockReturnValue(new Promise<UploadResponse>((r) => (resolve = r)));
    const { result } = renderUpload();

    await act(async () => {
      void result.current.uploadFiles([file("a.pdf")]);
    });
    expect(result.current.uploading).toBe(true);

    await act(() => result.current.uploadFiles([file("b.pdf")]));
    expect(mockUpload).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolve({ uploaded: [], errors: [] });
    });
    expect(result.current.uploading).toBe(false);
  });

  it("onInputChange delegates picked files and clears the input value", async () => {
    mockUpload.mockResolvedValue({ uploaded: [], errors: [] });
    const { result } = renderUpload();

    const target = { files: [file("cv.pdf")], value: "cv.pdf" };
    await act(async () => {
      result.current.onInputChange({ target } as unknown as ChangeEvent<HTMLInputElement>);
    });

    expect(target.value).toBe("");
    expect(mockUpload).toHaveBeenCalledTimes(1);
  });

  it("onInputChange is a no-op on an empty selection", () => {
    const { result } = renderUpload();
    const target = { files: null, value: "" };
    act(() => {
      result.current.onInputChange({ target } as unknown as ChangeEvent<HTMLInputElement>);
    });
    expect(mockUpload).not.toHaveBeenCalled();
  });
});

describe("useUploadDrop", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const dragEvent = (files: File[]) =>
    ({
      preventDefault: vi.fn(),
      dataTransfer: { files },
      relatedTarget: null,
      currentTarget: document.createElement("div"),
    }) as unknown as DragEvent;

  it("tracks drag-over state and filters dropped files by extension", () => {
    const uploadFiles = vi.fn();
    const { result } = renderHook(() => useUploadDrop(uploadFiles, false));

    act(() => result.current.dropHandlers.onDragOver(dragEvent([])));
    expect(result.current.dragActive).toBe(true);

    const pdf = file("cv.pdf");
    act(() => result.current.dropHandlers.onDrop(dragEvent([pdf, file("virus.exe")])));
    expect(result.current.dragActive).toBe(false);
    expect(uploadFiles).toHaveBeenCalledWith([pdf]);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Skipped 1 unsupported file"));
  });

  it("ignores drops while an upload is already in flight", () => {
    const uploadFiles = vi.fn();
    const { result } = renderHook(() => useUploadDrop(uploadFiles, true));

    act(() => result.current.dropHandlers.onDrop(dragEvent([file("cv.pdf")])));
    expect(uploadFiles).not.toHaveBeenCalled();
  });

  it("keeps dragActive while moving across the target's children", () => {
    const { result } = renderHook(() => useUploadDrop(vi.fn(), false));
    act(() => result.current.dropHandlers.onDragOver(dragEvent([])));

    const parent = document.createElement("div");
    const child = document.createElement("span");
    parent.appendChild(child);
    act(() =>
      result.current.dropHandlers.onDragLeave({
        preventDefault: vi.fn(),
        relatedTarget: child,
        currentTarget: parent,
      } as unknown as DragEvent)
    );
    expect(result.current.dragActive).toBe(true);

    act(() =>
      result.current.dropHandlers.onDragLeave({
        preventDefault: vi.fn(),
        relatedTarget: document.createElement("div"),
        currentTarget: parent,
      } as unknown as DragEvent)
    );
    expect(result.current.dragActive).toBe(false);
  });
});
