"use client";

import React, { useMemo } from "react";
import { AlertCircle, Check, ChevronDown, StopCircle, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapse } from "@/app/components/Collapse";
import { ToolCallBatchList } from "@/app/components/ToolCallBatchList";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import { useAutoCollapse } from "@/app/hooks/useAutoCollapse";
import { usePointerToggle } from "@/app/hooks/usePointerToggle";
import type { ActionRequest, ReviewConfig, ToolCall } from "@/app/types/types";
import { parseToolError } from "@/app/utils/toolErrors";
import { cn } from "@/lib/utils";

interface ToolCallGroupProps {
  /**
   * One consecutive run of non-`task` tool calls, batched by the AI message
   * that issued them — calls within a batch ran in parallel. Batches and
   * calls are non-empty.
   */
  batches: ToolCall[][];
  isLoading?: boolean;
  /**
   * The run is the transcript's last unit and may still grow another batch —
   * hold the group open across the model's think-pauses between batches.
   */
  isOpenEnded?: boolean;
  actionRequestsMap?: Map<string, ActionRequest>;
  reviewConfigsMap?: Map<string, ReviewConfig>;
  onResumeInterrupt?: (value: unknown) => void;
}

const MAX_SUMMARY_NAMES = 3;

/**
 * A consecutive run of main-agent tool calls (across AI messages, broken by
 * prose, a subagent, or a user message) as a single disclosure unit: expanded
 * with live rows while the run is active, then auto-collapsed to a
 * "N tool calls" summary row the moment it finishes. The expanded body keeps
 * the per-message batches visible — simultaneous calls get an "N in parallel"
 * label. A run of exactly one call renders as its plain ToolCallBox.
 */
export const ToolCallGroup = React.memo<ToolCallGroupProps>(
  ({ batches, isLoading, isOpenEnded, actionRequestsMap, reviewConfigsMap, onResumeInterrupt }) => {
    const toolCalls = useMemo(() => batches.flat(), [batches]);

    // `isLoading` gates the pendings so a reload, Stop, or interrupt pause all
    // read as terminal; `isOpenEnded` keeps the tip group of a live run open
    // between batches; a pending HITL approval pins the group open instead.
    const isRunning =
      !!isLoading && (!!isOpenEnded || toolCalls.some((tc) => tc.status === "pending"));
    const hasPendingInterrupt = toolCalls.some((tc) => tc.status === "interrupted");
    const { isExpanded, toggle } = useAutoCollapse(isRunning, {
      forceExpanded: hasPendingInterrupt,
    });

    // Skip pendings like ToolCallBox does — parseToolError JSON-stringifies
    // and would re-run per streamed token for nothing.
    const failedCount = useMemo(
      () =>
        toolCalls.filter(
          (tc) => tc.status === "error" || (tc.status !== "pending" && parseToolError(tc.result))
        ).length,
      [toolCalls]
    );

    const nameSummary = useMemo(() => {
      const names: string[] = [];
      for (const tc of toolCalls) {
        if (tc.name && !names.includes(tc.name)) names.push(tc.name);
      }
      return {
        shown: names.slice(0, MAX_SUMMARY_NAMES).join(" · "),
        extra: Math.max(0, names.length - MAX_SUMMARY_NAMES),
      };
    }, [toolCalls]);

    const { onPointerDown: handlePointerToggle, onClick: handleClickToggle } =
      usePointerToggle(toggle);

    const renderToolCall = (toolCall: ToolCall) => {
      // Route the approval request only to the call actually awaiting review —
      // a completed same-name call elsewhere in the run must not match.
      const interrupted = toolCall.status === "interrupted";
      return (
        <ToolCallBox
          key={toolCall.id}
          toolCall={toolCall}
          actionRequest={interrupted ? actionRequestsMap?.get(toolCall.name) : undefined}
          reviewConfig={interrupted ? reviewConfigsMap?.get(toolCall.name) : undefined}
          onResume={onResumeInterrupt}
          isLoading={isLoading}
        />
      );
    };

    if (toolCalls.length === 1) {
      return (
        <div className="relative mt-4 flex w-full flex-col gap-1.5 before:absolute before:top-2 before:bottom-2 before:left-[12px] before:w-px before:bg-border2">
          {renderToolCall(toolCalls[0])}
        </div>
      );
    }

    const allCompleted = toolCalls.every((tc) => tc.status === "completed");
    const statusNode = hasPendingInterrupt ? (
      <span className="grid size-[26px] place-items-center rounded-full bg-warning/10">
        <StopCircle size={14} className="text-warning" />
      </span>
    ) : isRunning ? (
      <span className="grid size-[26px] place-items-center rounded-full bg-brand-accent-soft">
        <span className="size-[13px] animate-spin rounded-full border-2 border-brand-accent border-t-transparent" />
      </span>
    ) : failedCount > 0 ? (
      <span className="grid size-[26px] place-items-center rounded-full bg-destructive/10">
        <AlertCircle size={14} className="text-destructive" />
      </span>
    ) : allCompleted ? (
      <span className="grid size-[26px] place-items-center rounded-full bg-success-soft">
        <Check size={13} className="text-success" strokeWidth={3} />
      </span>
    ) : (
      // Run stopped with calls still unresolved — hollow node, per the rail spec.
      <span className="grid size-[26px] place-items-center rounded-full">
        <span className="size-[13px] rounded-full border-2 border-border2" />
      </span>
    );

    return (
      <div
        className={cn(
          "relative mt-4 flex w-full flex-col",
          isExpanded &&
            "before:absolute before:top-2 before:bottom-2 before:left-[12px] before:w-px before:bg-border2"
        )}
      >
        {/* Summary row — same [node | content] geometry as ToolCallBox rows. */}
        <div className="relative grid grid-cols-[26px_minmax(0,1fr)] gap-3">
          <div className="relative z-10 flex justify-center pt-1">{statusNode}</div>
          <div className="min-w-0 overflow-hidden rounded-xl border border-transparent transition-colors hover:bg-surface3">
            <Button
              variant="ghost"
              size="sm"
              onPointerDown={handlePointerToggle}
              onClick={handleClickToggle}
              aria-expanded={isExpanded}
              className="relative z-10 flex h-auto w-full items-center justify-between gap-2 border-none px-2.5 py-2 text-left shadow-none outline-hidden hover:bg-transparent focus-visible:ring-1 focus-visible:ring-ring/40 focus-visible:ring-offset-0"
            >
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <Wrench size={15} className="shrink-0 text-tertiary" />
                <span className="shrink-0 text-[13px] font-medium text-primary">
                  {toolCalls.length} tool calls
                </span>
                {failedCount > 0 && (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-destructive/30 bg-destructive/10 px-1.5 py-0.5 text-[11px] leading-none font-medium text-destructive">
                    <AlertCircle size={12} />
                    {failedCount} failed
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-tertiary">
                  {nameSummary.shown}
                  {nameSummary.extra > 0 && ` +${nameSummary.extra}`}
                </span>
              </div>
              <ChevronDown
                size={14}
                className={cn(
                  "shrink-0 text-tertiary transition-transform duration-200",
                  !isExpanded && "-rotate-90"
                )}
              />
            </Button>
          </div>
        </div>
        <Collapse isExpanded={isExpanded}>
          <div className="pt-1.5">
            <ToolCallBatchList batches={batches} renderToolCall={renderToolCall} />
          </div>
        </Collapse>
      </div>
    );
  }
);

ToolCallGroup.displayName = "ToolCallGroup";
