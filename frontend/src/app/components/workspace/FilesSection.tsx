"use client";

import React, { useRef, useState } from "react";
import { FileText, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { FilesPopover } from "@/app/components/TasksFilesSidebar";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";
import { Button } from "@/components/ui/button";
import { CAREER_AGENT_UPLOAD_DIR, uploadAgentFiles } from "@/app/lib/uploadFiles";
import { useChatContext } from "@/providers/ChatProvider";

interface FilesSectionProps {
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
  removeFile: (virtualPath: string) => Promise<void>;
  editDisabled: boolean;
  open: boolean;
  onToggle: () => void;
}

const UPLOAD_ACCEPT = ".pdf,.doc,.docx,.txt,.md";

export function FilesSection({
  files,
  setFiles,
  removeFile,
  editDisabled,
  open,
  onToggle,
}: FilesSectionProps) {
  const count = Object.keys(files).length;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const { refreshFiles, appendUploadNote } = useChatContext();

  const handleSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    const picked = Array.from(list);
    e.target.value = "";

    setUploading(true);
    try {
      const res = await uploadAgentFiles({ files: picked, targetDir: CAREER_AGENT_UPLOAD_DIR });
      if (res.uploaded.length > 0) {
        toast.success(`Uploaded ${res.uploaded.length} file${res.uploaded.length > 1 ? "s" : ""}`);
        appendUploadNote(
          res.uploaded.map((u) => u.path.split("/").pop()).filter((n): n is string => !!n)
        );
      }
      for (const err of res.errors) {
        toast.error(`${err.name}: ${err.reason}`);
      }
      await refreshFiles?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const uploadButton = (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        onChange={handleSelect}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="gap-1.5"
        disabled={uploading || editDisabled}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
        <span>Upload</span>
      </Button>
    </>
  );

  return (
    <WorkspaceCard
      icon={<FileText size={18} />}
      title="Files"
      count={count}
      open={open}
      onToggle={onToggle}
      headerAction={uploadButton}
    >
      {count === 0 ? (
        <p className="py-2 text-sm text-muted-foreground">
          No files yet. Upload your CV or job description to get started.
        </p>
      ) : (
        <FilesPopover
          files={files}
          setFiles={setFiles}
          removeFile={removeFile}
          editDisabled={editDisabled}
        />
      )}
    </WorkspaceCard>
  );
}
