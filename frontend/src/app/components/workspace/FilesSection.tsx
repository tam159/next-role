"use client";

import { useRef } from "react";
import { FileText, Loader2, Upload } from "lucide-react";
import { FilesPopover } from "@/app/components/TasksFilesSidebar";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";
import { Button } from "@/components/ui/button";
import { useFileUpload } from "@/app/hooks/useFileUpload";
import { useUploadCue } from "@/app/hooks/useUploadCue";
import { UPLOAD_ACCEPT } from "@/app/lib/uploadFiles";

interface FilesSectionProps {
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
  removeFile: (virtualPath: string) => Promise<void>;
  removeFiles: (
    virtualPaths: string[]
  ) => Promise<{ deleted: string[]; errors: { path: string; reason: string }[] }>;
  editDisabled: boolean;
  open: boolean;
  onToggle: () => void;
}

export function FilesSection({
  files,
  setFiles,
  removeFile,
  removeFiles,
  editDisabled,
  open,
  onToggle,
}: FilesSectionProps) {
  const count = Object.keys(files).length;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const { uploading, uploadFiles, onInputChange } = useFileUpload();
  const { showPulseCue, dismissCue } = useUploadCue();

  // Dismiss on click, not on upload: opening the picker proves the user found
  // the button, which is all the cue exists for — cancelling still counts.
  const openPicker = () => {
    dismissCue();
    inputRef.current?.click();
  };

  const uploadButton = (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        onChange={onInputChange}
      />
      <span className="relative inline-flex">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={uploading || editDisabled}
          onClick={openPicker}
        >
          {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          <span>Upload</span>
        </Button>
        {showPulseCue && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute -top-0.5 -right-0.5 size-2 rounded-full bg-brand-accent ring-2 ring-surface-raised motion-safe:animate-pulse"
          />
        )}
      </span>
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
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <span className="flex size-10 items-center justify-center rounded-[9px] bg-brand-accent-soft text-brand-accent">
            <Upload size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-foreground">No files yet</p>
            <p className="mx-auto mt-1 max-w-[280px] text-sm text-muted-foreground">
              Add your resume or a job description and NextRole will tailor everything to it.
            </p>
          </div>
          <Button
            type="button"
            variant="primary"
            size="sm"
            className="gap-1.5"
            disabled={uploading || editDisabled}
            onClick={openPicker}
          >
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            Upload files
          </Button>
        </div>
      ) : (
        <FilesPopover
          files={files}
          setFiles={setFiles}
          removeFile={removeFile}
          removeFiles={removeFiles}
          editDisabled={editDisabled}
          onAddFiles={openPicker}
          onDropFiles={uploadFiles}
          uploading={uploading}
        />
      )}
    </WorkspaceCard>
  );
}
