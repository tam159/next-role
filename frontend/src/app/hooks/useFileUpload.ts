"use client";

import { type ChangeEvent, type DragEvent, useCallback, useState } from "react";
import { toast } from "sonner";
import {
  CAREER_AGENT_UPLOAD_DIR,
  isAcceptedUploadName,
  uploadAgentFiles,
} from "@/app/lib/uploadFiles";
import { useChatContext } from "@/providers/ChatProvider";

/**
 * Shared upload action for every surface that sends files to the agent's
 * `/upload` artifacts (Workspace > Files, composer paperclip, hero dropzone):
 * uploads, toasts results, appends the "Uploaded: ..." composer note, and
 * refreshes the merged file list.
 */
export function useFileUpload() {
  const { refreshFiles, appendUploadNote } = useChatContext();
  const [uploading, setUploading] = useState(false);

  const uploadFiles = useCallback(
    async (picked: File[]) => {
      if (picked.length === 0 || uploading) return;
      setUploading(true);
      try {
        const res = await uploadAgentFiles({ files: picked, targetDir: CAREER_AGENT_UPLOAD_DIR });
        if (res.uploaded.length > 0) {
          toast.success(
            `Uploaded ${res.uploaded.length} file${res.uploaded.length > 1 ? "s" : ""}`
          );
          appendUploadNote(
            res.uploaded.map((u) => u.path.split("/").pop()).filter((n): n is string => !!n)
          );
        }
        for (const err of res.errors) toast.error(`${err.name}: ${err.reason}`);
        await refreshFiles?.();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [appendUploadNote, refreshFiles, uploading]
  );

  const onInputChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files;
      if (!list || list.length === 0) return;
      const picked = Array.from(list);
      e.target.value = "";
      void uploadFiles(picked);
    },
    [uploadFiles]
  );

  return { uploading, uploadFiles, onInputChange };
}

/**
 * Drag-and-drop for an upload target (hero dropzone, files add tile): tracks
 * drag-over state and, on drop, filters against the accepted extensions,
 * toasts what got skipped, and hands the rest to `uploadFiles`. Scoped to the
 * element the handlers are spread on — no window-level listener.
 */
export function useUploadDrop(
  uploadFiles: (files: File[]) => void | Promise<void>,
  uploading: boolean
) {
  const [dragActive, setDragActive] = useState(false);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent) => {
    // Ignore leave events fired while moving across the target's own children.
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragActive(false);
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      if (uploading) return;
      const dropped = Array.from(e.dataTransfer.files);
      const accepted = dropped.filter((f) => isAcceptedUploadName(f.name));
      const skipped = dropped.length - accepted.length;
      if (skipped > 0) {
        toast.error(
          `Skipped ${skipped} unsupported file${skipped > 1 ? "s" : ""} — PDF, DOC, DOCX, TXT, MD only`
        );
      }
      if (accepted.length > 0) void uploadFiles(accepted);
    },
    [uploadFiles, uploading]
  );

  return { dragActive, dropHandlers: { onDragOver, onDragLeave, onDrop } };
}
