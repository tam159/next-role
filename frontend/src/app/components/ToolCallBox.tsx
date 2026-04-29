"use client";

import React, { useState, useMemo, useCallback } from "react";
import {
  Bot,
  ChevronDown,
  ChevronUp,
  FilePenLine,
  FileSearch,
  FolderTree,
  Globe2,
  ImageIcon,
  LucideIcon,
  Network,
  Search,
  Terminal,
  AlertCircle,
  Loader2,
  CircleCheckBigIcon,
  StopCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { cn } from "@/lib/utils";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { ToolApprovalInterrupt } from "@/app/components/ToolApprovalInterrupt";

interface ToolCallBoxProps {
  toolCall: ToolCall;
  uiComponent?: any;
  stream?: any;
  graphId?: string;
  actionRequest?: ActionRequest;
  reviewConfig?: ReviewConfig;
  onResume?: (value: any) => void;
  isLoading?: boolean;
}

const TOOL_ICON_MAP: Array<[RegExp, LucideIcon]> = [
  [/^(read_file|read|open_file)$/i, FileSearch],
  [/^(write_file|edit_file|create_file|patch)$/i, FilePenLine],
  [/^(ls|list|list_files)$/i, FolderTree],
  [/(search|grep|rg|web)/i, Search],
  [/(image|generate_social)/i, ImageIcon],
  [/^(task|subagent)$/i, Bot],
  [/(fetch|http|url|source)/i, Globe2],
  [/(mcp|network|api)/i, Network],
];

function getToolIcon(name: string): LucideIcon {
  return TOOL_ICON_MAP.find(([pattern]) => pattern.test(name))?.[1] ?? Terminal;
}

function parseToolError(result: unknown): { code?: string; message?: string } | null {
  if (result == null) return null;

  const text = typeof result === "string" ? result : JSON.stringify(result);
  if (!text) return null;
  const trimmed = text.trim();
  const startsWithError = /^(error|exception|failed)\b\s*:?\s*/i.test(trimmed);
  if (!startsWithError) return null;

  const objectStart = trimmed.indexOf("{");
  if (objectStart !== -1) {
    try {
      const parsed = JSON.parse(trimmed.slice(objectStart).replace(/'/g, '"'));
      const error = parsed?.error ?? parsed;
      if (error?.code || error?.status || error?.message) {
        return {
          code: error.code ? String(error.code) : error.status,
          message: error.message ? String(error.message) : trimmed,
        };
      }
    } catch {
      // Fall through to regex detection for Python-style dicts or plain strings.
    }
  }

  const codeMatch =
    trimmed.match(/\bcode['"]?\s*:\s*(\d{3,})/i) ?? trimmed.match(/\b(\d{3})\s+[A-Z_]+\b/);
  const statusMatch = trimmed.match(/\bstatus['"]?\s*:\s*['"]?([A-Z_]+)/i);

  return {
    code: codeMatch?.[1] ?? statusMatch?.[1],
    message: trimmed,
  };
}

function getStatusMeta(
  status: ToolCall["status"],
  parsedError: { code?: string; message?: string } | null
) {
  if (parsedError) {
    return {
      label: parsedError.code ? `Error ${parsedError.code}` : "Error",
      className: "border-destructive/30 bg-destructive/10 text-destructive",
      showLabel: true,
    };
  }

  switch (status) {
    case "completed":
      return {
        label: "Completed",
        className: "text-primary",
        showLabel: false,
      };
    case "error":
      return {
        label: "Failed",
        className: "border-destructive/30 bg-destructive/10 text-destructive",
        showLabel: true,
      };
    case "pending":
      return {
        label: "Running",
        className: "text-primary",
        showLabel: false,
      };
    case "interrupted":
      return {
        label: "Needs review",
        className: "border-warning/25 bg-warning/10 text-warning",
        showLabel: true,
      };
    default:
      return {
        label: "Tool",
        className: "border-border bg-muted text-muted-foreground",
        showLabel: true,
      };
  }
}

function previewValue(value: unknown): string | null {
  if (value == null) return null;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return null;
  return text.length > 96 ? `${text.slice(0, 96)}...` : text;
}

export const ToolCallBox = React.memo<ToolCallBoxProps>(
  ({
    toolCall,
    uiComponent,
    stream,
    graphId,
    actionRequest,
    reviewConfig,
    onResume,
    isLoading,
  }) => {
    const [isExpanded, setIsExpanded] = useState(() => !!uiComponent || !!actionRequest);
    const [expandedArgs, setExpandedArgs] = useState<Record<string, boolean>>({});

    const { name, args, result, status } = useMemo(() => {
      return {
        name: toolCall.name || "Unknown Tool",
        args: toolCall.args || {},
        result: toolCall.result,
        status: toolCall.status || "completed",
      };
    }, [toolCall]);

    const ToolIcon = useMemo(() => getToolIcon(name), [name]);
    const parsedError = useMemo(() => parseToolError(result), [result]);
    const visualStatus = parsedError ? "error" : status;
    const statusMeta = useMemo(() => getStatusMeta(status, parsedError), [status, parsedError]);
    const preview = useMemo(() => previewValue(result) ?? previewValue(args), [args, result]);

    const statusIcon = useMemo(() => {
      switch (status) {
        case "completed":
          return <CircleCheckBigIcon size={12} className="text-primary" />;
        case "error":
          return <AlertCircle size={12} className="text-destructive" />;
        case "pending":
          return <Loader2 size={12} className="animate-spin" />;
        case "interrupted":
          return <StopCircle size={12} className="text-warning" />;
        default:
          return <Terminal size={12} className="text-muted-foreground" />;
      }
    }, [status]);

    const visualStatusIcon = parsedError ? (
      <AlertCircle size={12} className="text-destructive" />
    ) : (
      statusIcon
    );

    const toggleExpanded = useCallback(() => {
      setIsExpanded((prev) => !prev);
    }, []);

    const toggleArgExpanded = useCallback((argKey: string) => {
      setExpandedArgs((prev) => ({
        ...prev,
        [argKey]: !prev[argKey],
      }));
    }, []);

    const hasContent = result || Object.keys(args).length > 0;

    return (
      <div
        className={cn(
          "hover:border-primary/25 relative w-full overflow-hidden rounded-xl border border-border bg-tool-surface shadow-[0_1px_0_rgba(255,255,255,0.35)_inset] outline-none transition-all duration-200 hover:bg-tool-surface-hover",
          isExpanded && hasContent && "border-primary/20 bg-surface-raised",
          visualStatus === "pending" && "tool-running-sweep",
          visualStatus === "error" && "border-destructive/30"
        )}
      >
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleExpanded}
          className={cn(
            "relative z-10 flex h-auto w-full items-center justify-between gap-3 border-none px-3 py-3 text-left shadow-none outline-none focus-visible:ring-1 focus-visible:ring-primary/40 focus-visible:ring-offset-0 disabled:cursor-default"
          )}
          disabled={!hasContent}
        >
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <span className="relative flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-card text-primary">
              {visualStatus === "pending" && (
                <span className="bg-primary/40 absolute size-2.5 animate-ping rounded-full" />
              )}
              <ToolIcon size={16} className="relative" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[15px] font-semibold tracking-[-0.3px] text-foreground">
                  {name}
                </span>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-full text-xs font-medium leading-none",
                    statusMeta.showLabel ? "border px-1.5 py-0.5" : "px-0.5 py-0",
                    statusMeta.className
                  )}
                  title={statusMeta.label}
                >
                  {visualStatusIcon}
                  {statusMeta.showLabel && <span>{statusMeta.label}</span>}
                  {!statusMeta.showLabel && <span className="sr-only">{statusMeta.label}</span>}
                </span>
              </div>
              {preview && !isExpanded && (
                <p className="mt-1 truncate text-xs text-muted-foreground">{preview}</p>
              )}
            </div>
          </div>
          <div className="relative z-10 flex shrink-0 items-center">
            {hasContent &&
              (isExpanded ? (
                <ChevronUp size={14} className="shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
              ))}
          </div>
        </Button>

        {isExpanded && hasContent && (
          <div className="relative z-10 px-4 pb-4">
            {uiComponent && stream && graphId ? (
              <div className="mt-3 overflow-hidden rounded-lg border border-border bg-card p-2">
                <LoadExternalComponent
                  key={uiComponent.id}
                  stream={stream}
                  message={uiComponent}
                  namespace={graphId}
                  meta={{ status, args, result: result ?? "No Result Yet" }}
                />
              </div>
            ) : actionRequest && onResume ? (
              // Show tool approval UI when there's an action request but no GenUI
              <div className="mt-3 overflow-hidden rounded-lg border border-amber-500/25 bg-amber-500/10 p-3">
                <ToolApprovalInterrupt
                  actionRequest={actionRequest}
                  reviewConfig={reviewConfig}
                  onResume={onResume}
                  isLoading={isLoading}
                />
              </div>
            ) : (
              <>
                {Object.keys(args).length > 0 && (
                  <div className="mt-3">
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Arguments
                    </h4>
                    <div className="space-y-2">
                      {Object.entries(args).map(([key, value]) => (
                        <div
                          key={key}
                          className="overflow-hidden rounded-lg border border-border bg-card"
                        >
                          <button
                            onClick={() => toggleArgExpanded(key)}
                            className="flex w-full items-center justify-between bg-muted/40 p-2 text-left text-xs font-medium transition-colors hover:bg-muted/70"
                          >
                            <span className="font-mono">{key}</span>
                            {expandedArgs[key] ? (
                              <ChevronUp size={12} className="text-muted-foreground" />
                            ) : (
                              <ChevronDown size={12} className="text-muted-foreground" />
                            )}
                          </button>
                          {expandedArgs[key] && (
                            <div className="border-t border-border bg-background/60 p-2">
                              <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-6 text-foreground">
                                {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {result && (
                  <div className="mt-3">
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Result
                    </h4>
                    <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-background/70 p-3 font-mono text-xs leading-7 text-foreground">
                      {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
                    </pre>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    );
  }
);

ToolCallBox.displayName = "ToolCallBox";
