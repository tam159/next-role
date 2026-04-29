"use client";

import React from "react";
import { FileText } from "lucide-react";
import { FilesPopover } from "@/app/components/TasksFilesSidebar";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";

interface FilesSectionProps {
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
  editDisabled: boolean;
  open: boolean;
  onToggle: () => void;
}

export function FilesSection({ files, setFiles, editDisabled, open, onToggle }: FilesSectionProps) {
  const count = Object.keys(files).length;
  return (
    <WorkspaceCard
      icon={<FileText size={18} />}
      title="Files"
      count={count}
      open={open}
      onToggle={onToggle}
    >
      {count === 0 ? (
        <p className="py-2 text-sm text-muted-foreground">No files yet</p>
      ) : (
        <FilesPopover files={files} setFiles={setFiles} editDisabled={editDisabled} />
      )}
    </WorkspaceCard>
  );
}
