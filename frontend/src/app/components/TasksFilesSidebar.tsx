"use client";

import React, { useMemo, useCallback, useState, useEffect, useRef } from "react";
import { FileText, CheckCircle, Circle, Clock, ChevronDown, Trash2, Loader2 } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import type { TodoItem, FileItem } from "@/app/types/types";
import { useChatContext } from "@/providers/ChatProvider";
import { cn } from "@/lib/utils";
import { FileViewDialog } from "@/app/components/FileViewDialog";

export function FilesPopover({
  files,
  setFiles,
  removeFile,
  editDisabled,
}: {
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
  removeFile: (virtualPath: string) => Promise<void>;
  editDisabled: boolean;
}) {
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleSaveFile = useCallback(
    async (fileName: string, content: string) => {
      await setFiles({ ...files, [fileName]: content });
      setSelectedFile({ path: fileName, content: content });
    },
    [files, setFiles]
  );

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setDeleting(true);
    try {
      await removeFile(target);
      const name = target.split("/").pop() || target;
      toast.success(`Deleted ${name}`);
      setPendingDelete(null);
      if (selectedFile?.path === target) setSelectedFile(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, removeFile, selectedFile]);

  const requestDelete = useCallback((path: string) => setPendingDelete(path), []);

  return (
    <>
      {Object.keys(files).length === 0 ? (
        <div className="flex h-full items-center justify-center p-4 text-center">
          <p className="text-xs text-muted-foreground">No files created yet</p>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2">
          {Object.keys(files).map((file) => {
            const filePath = String(file);
            const rawContent = files[file];
            let fileContent: string;
            if (typeof rawContent === "object" && rawContent !== null && "content" in rawContent) {
              const contentArray = (rawContent as { content: unknown }).content;
              if (Array.isArray(contentArray)) {
                fileContent = contentArray.join("\n");
              } else {
                fileContent = String(contentArray || "");
              }
            } else {
              fileContent = String(rawContent || "");
            }

            return (
              <div
                key={filePath}
                className="group relative"
                style={{ backgroundColor: "var(--color-file-button)" }}
              >
                <button
                  type="button"
                  onClick={() => setSelectedFile({ path: filePath, content: fileContent })}
                  className="hover:border-primary/25 w-full cursor-pointer space-y-2 truncate rounded-xl border border-border bg-transparent px-3 py-4 shadow-sm transition-colors"
                  onMouseEnter={(e) => {
                    e.currentTarget.parentElement!.style.backgroundColor =
                      "var(--color-file-button-hover)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.parentElement!.style.backgroundColor =
                      "var(--color-file-button)";
                  }}
                >
                  <FileText size={24} className="mx-auto text-primary" />
                  <span className="mx-auto block w-full truncate break-words text-center text-sm leading-relaxed text-foreground">
                    {filePath}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${filePath}`}
                  title="Delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    requestDelete(filePath);
                  }}
                  disabled={editDisabled}
                  className="absolute right-1.5 top-1.5 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/15 hover:text-destructive focus-visible:opacity-100 disabled:pointer-events-none group-hover:opacity-100"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {selectedFile && (
        <FileViewDialog
          file={selectedFile}
          onSaveFile={handleSaveFile}
          onDelete={requestDelete}
          onClose={() => setSelectedFile(null)}
          editDisabled={editDisabled}
        />
      )}

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogTitle>Delete file?</DialogTitle>
          <DialogDescription>
            <span className="font-mono text-foreground">{pendingDelete?.split("/").pop()}</span>{" "}
            will be permanently removed. This cannot be undone.
          </DialogDescription>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setPendingDelete(null)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleting}
            >
              {deleting && <Loader2 size={14} className="mr-1.5 animate-spin" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export const TasksFilesSidebar = React.memo<{
  todos: TodoItem[];
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
}>(({ todos, files, setFiles }) => {
  const { isLoading, interrupt, removeFile } = useChatContext();
  const [tasksOpen, setTasksOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);

  // Track previous counts to detect when content goes from empty to having items
  const prevTodosCount = useRef(todos.length);
  const prevFilesCount = useRef(Object.keys(files).length);

  // Auto-expand when todos go from empty to having content
  useEffect(() => {
    if (prevTodosCount.current === 0 && todos.length > 0) {
      setTasksOpen(true);
    }
    prevTodosCount.current = todos.length;
  }, [todos.length]);

  // Auto-expand when files go from empty to having content
  const filesCount = Object.keys(files).length;
  useEffect(() => {
    if (prevFilesCount.current === 0 && filesCount > 0) {
      setFilesOpen(true);
    }
    prevFilesCount.current = filesCount;
  }, [filesCount]);

  const getStatusIcon = useCallback((status: TodoItem["status"]) => {
    switch (status) {
      case "completed":
        return <CheckCircle size={12} className="text-success/80" />;
      case "in_progress":
        return <Clock size={12} className="text-warning/80" />;
      default:
        return <Circle size={10} className="text-tertiary/70" />;
    }
  }, []);

  const groupedTodos = useMemo(() => {
    return {
      pending: todos.filter((t) => t.status === "pending"),
      in_progress: todos.filter((t) => t.status === "in_progress"),
      completed: todos.filter((t) => t.status === "completed"),
    };
  }, [todos]);

  const groupedLabels = {
    pending: "Pending",
    in_progress: "In Progress",
    completed: "Completed",
  };

  return (
    <div className="min-h-0 w-full flex-1">
      <div className="font-inter flex h-full w-full flex-col p-0">
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
          <div className="flex items-center justify-between px-3 pb-1.5 pt-2">
            <span className="text-xs font-semibold tracking-wide text-zinc-600">AGENT TASKS</span>
            <button
              onClick={() => setTasksOpen((v) => !v)}
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-transform duration-200 hover:bg-muted",
                tasksOpen ? "rotate-180" : "rotate-0"
              )}
              aria-label="Toggle tasks panel"
            >
              <ChevronDown size={14} />
            </button>
          </div>
          {tasksOpen && (
            <div className="bg-muted-secondary rounded-xl px-3 pb-2">
              <ScrollArea className="h-full">
                {todos.length === 0 ? (
                  <div className="flex h-full items-center justify-center p-4 text-center">
                    <p className="text-xs text-muted-foreground">No tasks created yet</p>
                  </div>
                ) : (
                  <div className="ml-1 p-0.5">
                    {Object.entries(groupedTodos).map(([status, todos]) => (
                      <div className="mb-4">
                        <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-tertiary">
                          {groupedLabels[status as keyof typeof groupedLabels]}
                        </h3>
                        {todos.map((todo, index) => (
                          <div
                            key={`${status}_${todo.id}_${index}`}
                            className="mb-1.5 flex items-start gap-2 rounded-sm p-1 text-sm"
                          >
                            {getStatusIcon(todo.status)}
                            <span className="flex-1 break-words leading-relaxed text-inherit">
                              {todo.content}
                            </span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </div>
          )}

          <div className="flex items-center justify-between px-3 pb-1.5 pt-2">
            <span className="text-xs font-semibold tracking-wide text-zinc-600">FILE SYSTEM</span>
            <button
              onClick={() => setFilesOpen((v) => !v)}
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-transform duration-200 hover:bg-muted",
                filesOpen ? "rotate-180" : "rotate-0"
              )}
              aria-label="Toggle files panel"
            >
              <ChevronDown size={14} />
            </button>
          </div>
          {filesOpen && (
            <FilesPopover
              files={files}
              setFiles={setFiles}
              removeFile={removeFile}
              editDisabled={isLoading === true || interrupt !== undefined}
            />
          )}
        </div>
      </div>
    </div>
  );
});

TasksFilesSidebar.displayName = "TasksFilesSidebar";
