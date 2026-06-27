"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { FileViewDialog } from "@/app/components/FileViewDialog";
import { useChatContext } from "@/providers/ChatProvider";
import { normalizeFilePath } from "@/app/utils/filePaths";
import type { FileItem } from "@/app/types/types";

/**
 * Centralized file preview for chat-initiated opens (clickable paths in agent
 * replies). Resolves candidate paths against the real workspace files and opens
 * the shared FileViewDialog. Lives inside ChatProvider so it can read `files`.
 */
interface FilePreviewContextValue {
  /** Returns the real file key for a candidate path, or null if no such file. */
  resolveFile: (candidate: string) => string | null;
  /** Open the preview for a file key/path (no-op if it no longer exists). */
  openFile: (keyOrPath: string) => void;
}

const FilePreviewContext = createContext<FilePreviewContextValue | null>(null);

function extractContent(raw: unknown): string {
  if (typeof raw === "object" && raw !== null && "content" in raw) {
    const inner = (raw as { content: unknown }).content;
    return Array.isArray(inner) ? inner.join("\n") : String(inner ?? "");
  }
  return String(raw ?? "");
}

export function FilePreviewProvider({ children }: { children: React.ReactNode }) {
  const { files, setFiles, isLoading, interrupt } = useChatContext();
  const [previewKey, setPreviewKey] = useState<string | null>(null);

  // normalized path -> actual file key. Depends on `files`, so the context value
  // updates when files change — which re-renders consuming MarkdownContent and
  // re-linkifies history-loaded messages once their files finish loading.
  const lookup = useMemo(() => {
    const map = new Map<string, string>();
    for (const key of Object.keys(files)) map.set(normalizeFilePath(key), key);
    return map;
  }, [files]);

  const resolveFile = useCallback(
    (candidate: string) => lookup.get(normalizeFilePath(candidate)) ?? null,
    [lookup]
  );

  const openFile = useCallback(
    (keyOrPath: string) => {
      const actual = resolveFile(keyOrPath);
      // Defer to the next tick so the originating click fully settles before the
      // modal mounts its dismiss listeners — otherwise the same gesture is read
      // as a click-outside and closes the dialog immediately.
      if (actual) setTimeout(() => setPreviewKey(actual), 0);
    },
    [resolveFile]
  );

  const value = useMemo(() => ({ resolveFile, openFile }), [resolveFile, openFile]);

  const file: FileItem | null =
    previewKey != null && files[previewKey] !== undefined
      ? { path: previewKey, content: extractContent(files[previewKey]) }
      : null;

  const editDisabled = isLoading === true || interrupt !== undefined;

  return (
    <FilePreviewContext.Provider value={value}>
      {children}
      {file && (
        <FileViewDialog
          file={file}
          onSaveFile={async (name, content) => {
            await setFiles({ ...files, [name]: content });
            setPreviewKey(name);
          }}
          onClose={() => setPreviewKey(null)}
          editDisabled={editDisabled}
        />
      )}
    </FilePreviewContext.Provider>
  );
}

/** Returns the preview API, or null when rendered outside the provider (e.g. print page). */
export function useFilePreview(): FilePreviewContextValue | null {
  return useContext(FilePreviewContext);
}
