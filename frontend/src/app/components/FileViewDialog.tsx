"use client";

import React, { useMemo, useCallback, useState, useEffect } from "react";
import { FileText, Copy, Download, Edit, Save, X, Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { toast } from "sonner";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import type { FileItem } from "@/app/types/types";
import useSWRMutation from "swr/mutation";

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"]);
const BINARY_EXTS = new Set([
  ...IMAGE_EXTS,
  "pdf",
  "zip",
  "gz",
  "tar",
  "mp3",
  "mp4",
  "wav",
  "ogg",
  "woff",
  "woff2",
  "ttf",
  "otf",
]);

function imageMime(ext: string): string {
  if (ext === "jpg") return "image/jpeg";
  if (ext === "ico") return "image/x-icon";
  return `image/${ext}`;
}

const LANGUAGE_MAP: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  cpp: "cpp",
  c: "c",
  cs: "csharp",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  scala: "scala",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  json: "json",
  xml: "xml",
  html: "html",
  css: "css",
  scss: "scss",
  sass: "sass",
  less: "less",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  dockerfile: "dockerfile",
  makefile: "makefile",
};

export const FileViewDialog = React.memo<{
  file: FileItem | null;
  onSaveFile: (fileName: string, content: string) => Promise<void>;
  onClose: () => void;
  editDisabled: boolean;
}>(({ file, onSaveFile, onClose, editDisabled }) => {
  const [isEditingMode, setIsEditingMode] = useState(file === null);
  const [fileName, setFileName] = useState(String(file?.path || ""));
  const [fileContent, setFileContent] = useState(String(file?.content || ""));

  const fileUpdate = useSWRMutation(
    { kind: "files-update", fileName, fileContent },
    async ({ fileName, fileContent }) => {
      if (!fileName || !fileContent) return;
      return await onSaveFile(fileName, fileContent);
    },
    {
      onSuccess: () => setIsEditingMode(false),
      onError: (error) => toast.error(`Failed to save file: ${error}`),
    }
  );

  useEffect(() => {
    setFileName(String(file?.path || ""));
    setFileContent(String(file?.content || ""));
    setIsEditingMode(file === null);
  }, [file]);

  const fileExtension = useMemo(() => {
    const fileNameStr = String(fileName || "");
    return fileNameStr.split(".").pop()?.toLowerCase() || "";
  }, [fileName]);

  const isMarkdown = useMemo(() => {
    return fileExtension === "md" || fileExtension === "markdown";
  }, [fileExtension]);

  const isImage = useMemo(() => IMAGE_EXTS.has(fileExtension), [fileExtension]);
  const isBinary = useMemo(() => BINARY_EXTS.has(fileExtension), [fileExtension]);

  const language = useMemo(() => {
    return LANGUAGE_MAP[fileExtension] || "text";
  }, [fileExtension]);

  const handleCopy = useCallback(() => {
    if (fileContent) {
      navigator.clipboard.writeText(fileContent);
    }
  }, [fileContent]);

  const handleDownload = useCallback(() => {
    if (!fileContent || !fileName) return;
    let blob: Blob;
    if (isBinary) {
      const bytes = Uint8Array.from(atob(fileContent), (c) => c.charCodeAt(0));
      const mime = isImage ? imageMime(fileExtension) : "application/octet-stream";
      blob = new Blob([bytes], { type: mime });
    } else {
      blob = new Blob([fileContent], { type: "text/plain" });
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName.split("/").pop() || "file";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [fileContent, fileName, isBinary, isImage, fileExtension]);

  const handleEdit = useCallback(() => {
    setIsEditingMode(true);
  }, []);

  const handleCancel = useCallback(() => {
    if (file === null) {
      onClose();
    } else {
      setFileName(String(file.path));
      setFileContent(String(file.content));
      setIsEditingMode(false);
    }
  }, [file, onClose]);

  const fileNameIsValid = useMemo(() => {
    return fileName.trim() !== "" && !fileName.includes("/") && !fileName.includes(" ");
  }, [fileName]);

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="flex h-[80vh] max-h-[80vh] min-w-[60vw] flex-col p-6">
        <DialogTitle className="sr-only">{file?.path || "New File"}</DialogTitle>
        <DialogDescription className="sr-only">
          View, copy, download, or edit the selected file.
        </DialogDescription>
        <div className="mb-4 flex items-center justify-between border-b border-border pb-4">
          <div className="flex min-w-0 items-center gap-2">
            <FileText className="text-primary/50 h-5 w-5 shrink-0" />
            {isEditingMode && file === null ? (
              <Input
                value={fileName}
                onChange={(e) => setFileName(e.target.value)}
                placeholder="Enter filename..."
                className="text-base font-medium"
                aria-invalid={!fileNameIsValid}
              />
            ) : (
              <span className="overflow-hidden text-ellipsis whitespace-nowrap text-base font-medium text-primary">
                {file?.path}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {!isEditingMode && (
              <>
                <Button
                  onClick={handleEdit}
                  variant="ghost"
                  size="sm"
                  className="h-8 px-2"
                  disabled={editDisabled || isBinary}
                >
                  <Edit size={16} className="mr-1" />
                  Edit
                </Button>
                <Button onClick={handleCopy} variant="ghost" size="sm" className="h-8 px-2">
                  <Copy size={16} className="mr-1" />
                  Copy
                </Button>
                <Button onClick={handleDownload} variant="ghost" size="sm" className="h-8 px-2">
                  <Download size={16} className="mr-1" />
                  Download
                </Button>
              </>
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {isEditingMode ? (
            <Textarea
              value={fileContent}
              onChange={(e) => setFileContent(e.target.value)}
              placeholder="Enter file content..."
              className="h-full min-h-[400px] resize-none font-mono text-sm"
            />
          ) : (
            <ScrollArea className="h-full rounded-md bg-surface">
              <div className="p-4">
                {fileContent ? (
                  isImage ? (
                    <div className="flex items-center justify-center p-4">
                      <img
                        src={`data:${imageMime(fileExtension)};base64,${fileContent}`}
                        alt={fileName}
                        className="max-h-[60vh] max-w-full rounded-md object-contain"
                      />
                    </div>
                  ) : isBinary ? (
                    <div className="flex flex-col items-center justify-center gap-2 p-12">
                      <p className="text-sm text-muted-foreground">
                        Binary file ({fileExtension}). Use Download to save it.
                      </p>
                    </div>
                  ) : isMarkdown ? (
                    <div className="rounded-md p-6">
                      <MarkdownContent content={fileContent} />
                    </div>
                  ) : (
                    <SyntaxHighlighter
                      language={language}
                      style={oneDark}
                      customStyle={{
                        margin: 0,
                        borderRadius: "0.5rem",
                        fontSize: "0.875rem",
                      }}
                      showLineNumbers
                      wrapLines={true}
                      lineProps={{
                        style: {
                          whiteSpace: "pre-wrap",
                        },
                      }}
                    >
                      {fileContent}
                    </SyntaxHighlighter>
                  )
                ) : (
                  <div className="flex items-center justify-center p-12">
                    <p className="text-sm text-muted-foreground">File is empty</p>
                  </div>
                )}
              </div>
            </ScrollArea>
          )}
        </div>
        {isEditingMode && (
          <div className="mt-4 flex justify-end gap-2 border-t border-border pt-4">
            <Button onClick={handleCancel} variant="outline" size="sm">
              <X size={16} className="mr-1" />
              Cancel
            </Button>
            <Button
              onClick={() => fileUpdate.trigger()}
              size="sm"
              disabled={
                fileUpdate.isMutating ||
                !fileName.trim() ||
                !fileContent.trim() ||
                (file === null && !fileNameIsValid)
              }
            >
              {fileUpdate.isMutating ? (
                <Loader2 size={16} className="mr-1 animate-spin" />
              ) : (
                <Save size={16} className="mr-1" />
              )}
              Save
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
});

FileViewDialog.displayName = "FileViewDialog";
