"use client";

import React, { useMemo, useCallback, useState, useEffect, useRef } from "react";
import {
  FileText,
  CheckCircle,
  Circle,
  Clock,
  ChevronDown,
  Trash2,
  Loader2,
  Check,
} from "lucide-react";
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
import { getFileCategory, splitBasename, splitFilePath } from "@/app/lib/fileCategories";

function FileCard({
  filePath,
  fileContent,
  editDisabled,
  selected,
  onOpen,
  onRequestDelete,
  onToggleSelect,
}: {
  filePath: string;
  fileContent: string;
  editDisabled: boolean;
  selected: boolean;
  onOpen: (file: FileItem) => void;
  onRequestDelete: (path: string) => void;
  onToggleSelect: (path: string, event: React.MouseEvent) => void;
}) {
  const category = getFileCategory(filePath);
  const { prefix, basename } = splitFilePath(filePath);
  const { stem, ext } = splitBasename(basename);
  const iconColor = category?.iconVar ?? "var(--color-primary)";

  const nameRef = useRef<HTMLSpanElement>(null);
  const [isTruncated, setIsTruncated] = useState(false);

  useEffect(() => {
    const el = nameRef.current;
    if (!el) return;
    const check = () => setIsTruncated(el.scrollWidth > el.clientWidth);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [prefix, stem]);

  return (
    <div
      className={cn(
        "group relative rounded-xl transition-shadow",
        selected && "ring-2 ring-primary/40"
      )}
      style={{ backgroundColor: "var(--color-file-button)" }}
    >
      <button
        type="button"
        onClick={() => onOpen({ path: filePath, content: fileContent })}
        title={filePath}
        className="hover:border-primary/25 w-full cursor-pointer space-y-2 rounded-xl border border-border bg-transparent px-3 py-4 shadow-sm transition-colors"
        onMouseEnter={(e) => {
          e.currentTarget.parentElement!.style.backgroundColor = "var(--color-file-button-hover)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.parentElement!.style.backgroundColor = "var(--color-file-button)";
        }}
      >
        <FileText size={24} className="mx-auto" style={{ color: iconColor }} />
        <span className="flex w-full min-w-0 items-baseline text-sm leading-relaxed text-foreground">
          <span ref={nameRef} className="min-w-0 flex-1 truncate">
            {prefix}
            {stem}
          </span>
          {ext && (
            <span className={cn("shrink-0 font-semibold", isTruncated && "ml-1")}>{ext}</span>
          )}
        </span>
      </button>
      <button
        type="button"
        role="checkbox"
        aria-checked={selected}
        aria-label={selected ? `Deselect ${filePath}` : `Select ${filePath}`}
        title={selected ? "Deselect" : "Select"}
        onClick={(e) => {
          e.stopPropagation();
          onToggleSelect(filePath, e);
        }}
        className={cn(
          "absolute left-1.5 top-1.5 inline-flex size-5 items-center justify-center rounded border transition-opacity",
          "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
          selected
            ? "text-primary-foreground border-primary bg-primary opacity-100"
            : "hover:border-primary/60 border-border bg-background text-transparent opacity-0 group-hover:opacity-100"
        )}
      >
        <Check size={12} strokeWidth={3} />
      </button>
      <button
        type="button"
        aria-label={`Delete ${filePath}`}
        title="Delete"
        onClick={(e) => {
          e.stopPropagation();
          onRequestDelete(filePath);
        }}
        disabled={editDisabled}
        className="absolute right-1.5 top-1.5 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/15 hover:text-destructive focus-visible:opacity-100 disabled:pointer-events-none group-hover:opacity-100"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

const DELETE_PREVIEW_LIMIT = 5;

export function FilesPopover({
  files,
  setFiles,
  removeFile,
  removeFiles,
  editDisabled,
}: {
  files: Record<string, string>;
  setFiles: (files: Record<string, string>) => Promise<void>;
  removeFile: (virtualPath: string) => Promise<void>;
  removeFiles: (
    virtualPaths: string[]
  ) => Promise<{ deleted: string[]; errors: { path: string; reason: string }[] }>;
  editDisabled: boolean;
}) {
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const filePaths = useMemo(() => Object.keys(files), [files]);

  // Drop selection entries for files that no longer exist (e.g., after a refresh
  // that removed something while a stale set was held).
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set<string>();
      let changed = false;
      for (const p of prev) {
        if (files[p] !== undefined) valid.add(p);
        else changed = true;
      }
      return changed ? valid : prev;
    });
  }, [files]);

  const handleSaveFile = useCallback(
    async (fileName: string, content: string) => {
      await setFiles({ ...files, [fileName]: content });
      setSelectedFile({ path: fileName, content: content });
    },
    [files, setFiles]
  );

  const clearSelection = useCallback(() => {
    setSelected(new Set());
    setAnchor(null);
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(filePaths));
  }, [filePaths]);

  const onToggleSelect = useCallback(
    (path: string, event: React.MouseEvent) => {
      const isShift = event.shiftKey;
      setSelected((prev) => {
        const next = new Set(prev);
        if (isShift && anchor && anchor !== path) {
          const i = filePaths.indexOf(anchor);
          const j = filePaths.indexOf(path);
          if (i !== -1 && j !== -1) {
            const [lo, hi] = i < j ? [i, j] : [j, i];
            // Shift extends the selection — add the range, don't toggle off existing.
            for (let k = lo; k <= hi; k++) next.add(filePaths[k]);
            return next;
          }
        }
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });
      // Anchor follows the most recent direct click (range extensions keep the
      // existing anchor so successive shift-clicks pivot from it).
      if (!isShift) setAnchor(path);
    },
    [anchor, filePaths]
  );

  const onWrapperKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape" && selected.size > 0) {
        e.preventDefault();
        clearSelection();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
        if (filePaths.length === 0) return;
        e.preventDefault();
        selectAll();
      }
    },
    [selected.size, clearSelection, selectAll, filePaths.length]
  );

  // Auto-focus the wrapper when a selection appears so Esc / Cmd+A work
  // without the user having to click into empty space first.
  useEffect(() => {
    if (selected.size > 0) wrapperRef.current?.focus();
  }, [selected.size]);

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete || pendingDelete.length === 0) return;
    const targets = pendingDelete;
    setDeleting(true);
    try {
      if (targets.length === 1) {
        const target = targets[0];
        await removeFile(target);
        const name = target.split("/").pop() || target;
        toast.success(`Deleted ${name}`);
        if (selectedFile?.path === target) setSelectedFile(null);
      } else {
        const { deleted, errors } = await removeFiles(targets);
        if (errors.length === 0) {
          toast.success(`Deleted ${deleted.length} files`);
        } else if (deleted.length === 0) {
          toast.error(`Failed to delete ${errors.length} file${errors.length === 1 ? "" : "s"}`);
        } else {
          toast.warning(`Deleted ${deleted.length} of ${targets.length} (${errors.length} failed)`);
        }
        if (selectedFile && deleted.includes(selectedFile.path)) setSelectedFile(null);
        // Keep failed paths selected so the user can see what's left; drop the rest.
        const failedPaths = new Set(errors.map((e) => e.path));
        setSelected(failedPaths);
        if (failedPaths.size === 0) setAnchor(null);
      }
      setPendingDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, removeFile, removeFiles, selectedFile]);

  const requestDelete = useCallback((path: string) => setPendingDelete([path]), []);

  const pendingPaths = pendingDelete ?? [];

  return (
    <>
      {filePaths.length === 0 ? (
        <div className="flex h-full items-center justify-center p-4 text-center">
          <p className="text-xs text-muted-foreground">No files created yet</p>
        </div>
      ) : (
        <div ref={wrapperRef} tabIndex={-1} onKeyDown={onWrapperKeyDown} className="outline-none">
          {selected.size > 0 && (
            <div className="bg-muted-secondary sticky top-0 z-10 mb-2 flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
              <span className="text-muted-foreground">
                <span className="font-medium text-foreground">{selected.size}</span> selected
              </span>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={selectAll}
                  disabled={selected.size === filePaths.length}
                >
                  Select all
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={clearSelection}>
                  Clear
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => setPendingDelete([...selected])}
                  disabled={editDisabled}
                >
                  <Trash2 size={14} className="mr-1.5" />
                  Delete
                </Button>
              </div>
            </div>
          )}
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2">
            {filePaths.map((file) => {
              const filePath = String(file);
              const rawContent = files[file];
              let fileContent: string;
              if (
                typeof rawContent === "object" &&
                rawContent !== null &&
                "content" in rawContent
              ) {
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
                <FileCard
                  key={filePath}
                  filePath={filePath}
                  fileContent={fileContent}
                  editDisabled={editDisabled}
                  selected={selected.has(filePath)}
                  onOpen={setSelectedFile}
                  onRequestDelete={requestDelete}
                  onToggleSelect={onToggleSelect}
                />
              );
            })}
          </div>
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
        <DialogContent className={pendingPaths.length > 1 ? "sm:max-w-2xl" : "max-w-md"}>
          <DialogTitle>
            {pendingDelete && pendingDelete.length > 1
              ? `Delete ${pendingDelete.length} files?`
              : "Delete file?"}
          </DialogTitle>
          <DialogDescription asChild>
            <div>
              {pendingPaths.length === 1 ? (
                <>
                  <span className="font-mono text-foreground">
                    {splitFilePath(pendingPaths[0]).basename}
                  </span>{" "}
                  will be permanently removed. This cannot be undone.
                </>
              ) : (
                <>
                  <span>
                    The following files will be permanently removed. This cannot be undone.
                  </span>
                  <ul className="mt-2 max-h-72 list-disc space-y-1 overflow-y-auto pl-5 font-mono text-foreground">
                    {pendingPaths.slice(0, DELETE_PREVIEW_LIMIT).map((p) => (
                      <li key={p} className="break-words">
                        {p}
                      </li>
                    ))}
                  </ul>
                  {pendingPaths.length > DELETE_PREVIEW_LIMIT && (
                    <span className="mt-1 block text-xs text-muted-foreground">
                      and {pendingPaths.length - DELETE_PREVIEW_LIMIT} more
                    </span>
                  )}
                </>
              )}
            </div>
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
  const { isLoading, interrupt, removeFile, removeFiles } = useChatContext();
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
              removeFiles={removeFiles}
              editDisabled={isLoading === true || interrupt !== undefined}
            />
          )}
        </div>
      </div>
    </div>
  );
});

TasksFilesSidebar.displayName = "TasksFilesSidebar";
