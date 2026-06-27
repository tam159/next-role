"use client";

import React, { useState, useMemo, useCallback, useRef } from "react";
import {
  Bot,
  BookOpen,
  ChevronDown,
  ChevronUp,
  FilePenLine,
  FolderTree,
  Globe2,
  ImageIcon,
  LucideIcon,
  Network,
  Search,
  Terminal,
  SquareTerminal,
  AlertCircle,
  Loader2,
  CircleCheckBigIcon,
  StopCircle,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { cn } from "@/lib/utils";
import { ToolApprovalInterrupt } from "@/app/components/ToolApprovalInterrupt";

interface ToolCallBoxProps {
  toolCall: ToolCall;
  actionRequest?: ActionRequest;
  reviewConfig?: ReviewConfig;
  onResume?: (value: unknown) => void;
  isLoading?: boolean;
}

const TOOL_ICON_MAP: Array<[RegExp, LucideIcon]> = [
  [/^(read_file|read|open_file|cat)$/i, BookOpen],
  [/^(write_file|overwrite_file|edit_file|create_file|patch|write_todos)$/i, FilePenLine],
  [/^(ls|list|list_files)$/i, FolderTree],
  [/^(execute|run|bash|shell|exec)$/i, SquareTerminal],
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

  // Cap inspection at 256 chars — error markers always appear at the start, and
  // a long streaming payload here would force a JSON.stringify of the full body.
  const raw =
    typeof result === "string"
      ? result
      : (() => {
          try {
            return JSON.stringify(result);
          } catch {
            return "";
          }
        })();
  if (!raw) return null;
  const text = raw.length > 256 ? raw.slice(0, 256) : raw;
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
  if (typeof value === "string") {
    return value.length > 96 ? `${value.slice(0, 96)}...` : value;
  }
  // For objects/arrays we only need the first ~96 chars of the stringified form.
  // JSON.stringify on a large streaming payload here is the dominant cost during
  // streaming, so guard with try/catch and slice the result.
  try {
    const text = JSON.stringify(value);
    if (!text) return null;
    return text.length > 96 ? `${text.slice(0, 96)}...` : text;
  } catch {
    return null;
  }
}

export const ToolCallBox = React.memo<ToolCallBoxProps>(
  ({ toolCall, actionRequest, reviewConfig, onResume, isLoading }) => {
    const [isExpanded, setIsExpanded] = useState(() => !!actionRequest);
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
    // While the tool is streaming (`pending`), args grows by one token at a time.
    // Skip parseToolError + previewValue until completion — both call JSON.stringify
    // and would re-run on every token despite only feeding the collapsed header.
    const parsedError = useMemo(
      () => (status === "pending" ? null : parseToolError(result)),
      [result, status]
    );
    const visualStatus = parsedError ? "error" : status;
    const statusMeta = useMemo(() => getStatusMeta(status, parsedError), [status, parsedError]);
    const preview = useMemo(() => {
      if (status === "pending") return null;
      return previewValue(result) ?? previewValue(args);
    }, [args, result, status]);

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

    // Timeline-rail status node (running ring / done check / hollow). Sits on the
    // vertical rail drawn by the parent list container.
    const statusNode = (() => {
      switch (visualStatus) {
        case "completed":
          return (
            <span className="grid size-[26px] place-items-center rounded-full bg-success-soft">
              <Check size={13} className="text-success" strokeWidth={3} />
            </span>
          );
        case "error":
          return (
            <span className="grid size-[26px] place-items-center rounded-full bg-destructive/10">
              <AlertCircle size={14} className="text-destructive" />
            </span>
          );
        case "interrupted":
          return (
            <span className="grid size-[26px] place-items-center rounded-full bg-warning/10">
              <StopCircle size={14} className="text-warning" />
            </span>
          );
        default: // pending === running
          return (
            <span className="grid size-[26px] place-items-center rounded-full bg-brand-accent-soft">
              <span className="size-[13px] animate-spin rounded-full border-2 border-brand-accent border-t-transparent" />
            </span>
          );
      }
    })();

    // `onPointerDown` fires before the OS click-eating window during heavy
    // main-thread work (Safari, mid-stream). Keep `onClick` for keyboard
    // activation (Enter/Space → only fires click), and dedupe with a timestamp
    // so a single tap doesn't toggle twice.
    const lastPointerToggleRef = useRef(0);
    const handlePointerToggle = useCallback(() => {
      lastPointerToggleRef.current = performance.now();
      setIsExpanded((prev) => !prev);
    }, []);
    const handleClickToggle = useCallback(() => {
      if (performance.now() - lastPointerToggleRef.current < 300) return;
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
      <div className="relative grid grid-cols-[26px_minmax(0,1fr)] gap-3">
        {/* Status node — sits on the vertical rail drawn by the parent list. */}
        <div className="relative z-10 flex justify-center pt-1">{statusNode}</div>

        {/* Row content */}
        <div
          className={cn(
            "relative min-w-0 overflow-hidden rounded-xl outline-hidden transition-colors",
            isExpanded && hasContent
              ? "border border-primary bg-surface-raised shadow-xs"
              : "border border-transparent hover:bg-surface3",
            visualStatus === "pending" && "tool-running-sweep",
            visualStatus === "error" && "border border-destructive/30"
          )}
        >
          <Button
            variant="ghost"
            size="sm"
            onPointerDown={handlePointerToggle}
            onClick={handleClickToggle}
            className="relative z-10 flex h-auto w-full items-center justify-between gap-2 border-none px-2.5 py-2 text-left shadow-none outline-hidden hover:bg-transparent focus-visible:ring-1 focus-visible:ring-ring/40 focus-visible:ring-offset-0 disabled:cursor-default"
            disabled={!hasContent}
          >
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <ToolIcon size={15} className="shrink-0 text-tertiary" />
              <span className="shrink-0 font-mono text-[12.5px] font-medium text-primary">
                {name}
              </span>
              {statusMeta.showLabel && (
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px] leading-none font-medium",
                    statusMeta.className
                  )}
                  title={statusMeta.label}
                >
                  {visualStatusIcon}
                  <span>{statusMeta.label}</span>
                </span>
              )}
              {preview && !isExpanded && (
                <span className="min-w-0 flex-1 truncate text-xs text-secondary">{preview}</span>
              )}
            </div>
            {hasContent && (
              <ChevronDown
                size={14}
                className={cn(
                  "shrink-0 text-tertiary transition-transform duration-200",
                  isExpanded && "rotate-180"
                )}
              />
            )}
          </Button>

          {isExpanded && hasContent && (
            <div className="relative z-10 px-3 pb-3">
              {actionRequest && onResume ? (
                // Show tool approval UI when there's an action request but no GenUI
                <div className="mt-2 overflow-hidden rounded-lg border border-warning/30 bg-warning/10 p-3">
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
                    <div className="mt-2">
                      <h4 className="mb-1 text-[11px] font-semibold tracking-wider text-tertiary uppercase">
                        Arguments
                      </h4>
                      <div className="space-y-2">
                        {Object.entries(args).map(([key, value]) => (
                          <div
                            key={key}
                            className="overflow-hidden rounded-lg border border-primary bg-surface-raised"
                          >
                            <button
                              onClick={() => toggleArgExpanded(key)}
                              className="flex w-full items-center justify-between bg-surface3 p-2 text-left text-xs font-medium transition-colors hover:bg-surface3/70"
                            >
                              <span className="font-mono text-secondary">{key}</span>
                              {expandedArgs[key] ? (
                                <ChevronUp size={12} className="text-tertiary" />
                              ) : (
                                <ChevronDown size={12} className="text-tertiary" />
                              )}
                            </button>
                            {expandedArgs[key] && (
                              <div className="border-t border-primary bg-surface3 p-2">
                                <pre className="m-0 overflow-x-auto font-mono text-xs leading-6 break-all whitespace-pre-wrap text-primary">
                                  {typeof value === "string"
                                    ? value
                                    : JSON.stringify(value, null, 2)}
                                </pre>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {result && (
                    <div className="mt-2">
                      <h4 className="mb-1 text-[11px] font-semibold tracking-wider text-tertiary uppercase">
                        Result
                      </h4>
                      <pre className="m-0 overflow-x-auto rounded-lg border border-primary bg-surface3 p-3 font-mono text-xs leading-7 break-all whitespace-pre-wrap text-primary">
                        {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }
);

ToolCallBox.displayName = "ToolCallBox";
