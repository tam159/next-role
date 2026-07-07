"use client";

import React, { useMemo } from "react";
import {
  useMessages,
  useToolCalls,
  type AnyStream,
  type SubagentDiscoverySnapshot,
} from "@langchain/react";
import { type AIMessageChunk, isAIMessage } from "@langchain/core/messages";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Loader2,
  MessagesSquare,
  Radar,
  Scissors,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapse } from "@/app/components/Collapse";
import { ToolCallBatchList } from "@/app/components/ToolCallBatchList";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { useAutoCollapse } from "@/app/hooks/useAutoCollapse";
import { usePointerToggle } from "@/app/hooks/usePointerToggle";
import type { SubAgent, ToolCall } from "@/app/types/types";
import {
  extractSubAgentContent,
  formatDuration,
  parsePartialArgs,
  toResultString,
  unwrapToolPayload,
} from "@/app/utils/utils";
import { cn } from "@/lib/utils";

function mapSnapshotStatus(status: SubagentDiscoverySnapshot["status"]): SubAgent["status"] {
  if (status === "running") return "active";
  if (status === "error") return "error";
  return "completed";
}

// Identity glyph per known subagent; anything undeclared keeps the robot.
const SUBAGENT_ICON_MAP: Record<string, LucideIcon> = {
  "hiring-recon": Radar,
  "resume-tailor": Scissors,
  "interview-coach": MessagesSquare,
};

function getSubAgentStatusMeta(status: SubAgent["status"]) {
  switch (status) {
    case "active":
      return {
        label: "Running",
        icon: Loader2,
        className: "border-primary/25 bg-primary/10 text-primary",
        iconClassName: "animate-spin",
      };
    case "completed":
      return {
        label: "Complete",
        icon: CheckCircle2,
        className: "border-success/25 bg-success/10 text-success",
        iconClassName: "",
      };
    case "error":
      return {
        label: "Failed",
        icon: AlertCircle,
        className: "border-destructive/30 bg-destructive/10 text-destructive",
        iconClassName: "",
      };
    default:
      return {
        label: "Queued",
        icon: Clock3,
        className: "border-muted bg-muted/60 text-muted-foreground",
        iconClassName: "",
      };
  }
}

const noop = () => {};

interface SubagentHeaderProps {
  name: string;
  status: SubAgent["status"];
  toolCount?: number;
  durationLabel?: string | null;
  isExpanded?: boolean;
  onToggle?: () => void;
}

/**
 * The always-visible header row of a subagent card: identity chip + status
 * badge + tool-count pill + duration, and (when the card has a body) the
 * collapse toggle. Also serves the queued state, so the queued → running
 * transition doesn't jump.
 */
function SubagentHeader({
  name,
  status,
  toolCount,
  durationLabel,
  isExpanded,
  onToggle,
}: SubagentHeaderProps) {
  const statusMeta = getSubAgentStatusMeta(status);
  const StatusIcon = statusMeta.icon;
  const AgentIcon = SUBAGENT_ICON_MAP[name] ?? Bot;
  const { onPointerDown, onClick } = usePointerToggle(onToggle ?? noop);

  const content = (
    <>
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <AgentIcon size={16} />
        </span>
        <span className="min-w-0 truncate font-sans text-[15px] leading-[140%] font-bold tracking-[-0.4px] text-foreground">
          {name}
        </span>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
            statusMeta.className
          )}
        >
          <StatusIcon size={12} className={statusMeta.iconClassName} />
          {statusMeta.label}
        </span>
        {toolCount != null && toolCount > 0 && (
          <span className="inline-flex h-5 shrink-0 items-center rounded-full bg-surface3 px-2 text-[11px] font-semibold text-secondary">
            {toolCount} {toolCount === 1 ? "tool" : "tools"}
          </span>
        )}
        {durationLabel && <span className="shrink-0 text-xs text-tertiary">{durationLabel}</span>}
      </div>
      {onToggle && (
        <ChevronDown
          size={14}
          className={cn(
            "shrink-0 text-muted-foreground transition-transform duration-200",
            !isExpanded && "-rotate-90"
          )}
        />
      )}
    </>
  );

  return (
    <div className={cn("relative overflow-hidden", status === "active" && "tool-running-sweep")}>
      {onToggle ? (
        <Button
          variant="ghost"
          size="sm"
          onPointerDown={onPointerDown}
          onClick={onClick}
          aria-expanded={isExpanded}
          className="relative z-10 flex h-auto w-full items-center justify-between gap-3 border-none px-3 py-3 text-left shadow-none outline-hidden transition-colors duration-200 hover:bg-transparent"
        >
          {content}
        </Button>
      ) : (
        <div className="relative z-10 flex w-full items-center justify-between gap-3 px-3 py-3">
          {content}
        </div>
      )}
    </div>
  );
}

const cardClassName =
  "w-full overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-xs";

/**
 * Placeholder card for a `task` call whose discovery snapshot hasn't landed
 * yet (args still streaming). Hook-free on purpose: the scoped selector hooks
 * treat an undefined target as the ROOT namespace, so they must not be called
 * before a snapshot exists.
 */
export function QueuedSubagentCard({ name }: { name: string }) {
  return (
    <div className={cardClassName}>
      <SubagentHeader name={name} status="pending" />
    </div>
  );
}

interface SubagentCardProps {
  stream: AnyStream;
  snapshot: SubagentDiscoverySnapshot;
  taskToolCall: ToolCall;
  isLoading?: boolean;
}

/**
 * One subagent as a single disclosure card: an always-visible header row over
 * a collapsible INPUT / ACTIVITY / OUTPUT body. Expanded while the subagent
 * runs, auto-collapsed to the header the moment it completes.
 *
 * Mounting subscribes to the subagent's namespaced projections (ref-counted;
 * shared with any other consumer):
 *
 *  - `tools` — authoritative call lifecycle (`tool-started/finished/error`)
 *    with parsed args and outputs. No arg deltas on this channel.
 *  - `messages` — the subagent's own LLM stream; a call whose args are
 *    still streaming exists only as `tool_call_chunks` here, so this is
 *    what makes nested tool args grow token-by-token live.
 */
export const SubagentCard = React.memo<SubagentCardProps>(
  ({ stream, snapshot, taskToolCall, isLoading }) => {
    const assembled = useToolCalls(stream, snapshot);
    const messages = useMessages(stream, snapshot);

    // snapshot.status is the authority (trailing namespaced events after
    // `complete` can't hold the card open); the isLoading gate makes a
    // stopped run read as terminal even if its snapshot is stuck "running".
    const isRunning = snapshot.status === "running" && !!isLoading;
    const { isExpanded, toggle } = useAutoCollapse(isRunning);

    const nestedBatches: ToolCall[][] = useMemo(() => {
      // Authoritative status/result per call from the tools channel.
      const fromTools = new Map<string, ToolCall>();
      for (const tc of assembled) {
        if (tc.name === "task") continue;
        fromTools.set(tc.id, {
          id: tc.id,
          name: tc.name,
          args: (tc.args ?? {}) as Record<string, unknown>,
          result: toResultString(unwrapToolPayload(tc.output)),
          status:
            tc.status === "error" ? "error" : tc.status === "finished" ? "completed" : "pending",
        });
      }
      // Batch by issuing AI message — calls in one message ran in parallel.
      // A call whose args are still streaming exists only as a
      // tool_call_chunk until it finishes assembling.
      const batches: ToolCall[][] = [];
      const seen = new Set<string>();
      for (const message of messages) {
        if (!isAIMessage(message)) continue;
        const batch: ToolCall[] = [];
        for (const tc of message.tool_calls ?? []) {
          if (!tc.id || !tc.name || tc.name === "task" || seen.has(tc.id)) continue;
          seen.add(tc.id);
          batch.push(
            fromTools.get(tc.id) ?? {
              id: tc.id,
              name: tc.name,
              args: (tc.args ?? {}) as Record<string, unknown>,
              status: "pending",
            }
          );
        }
        for (const chunk of (message as AIMessageChunk).tool_call_chunks ?? []) {
          if (!chunk.id || !chunk.name || chunk.name === "task" || seen.has(chunk.id)) continue;
          seen.add(chunk.id);
          batch.push({
            id: chunk.id,
            name: chunk.name,
            args: parsePartialArgs(chunk.args),
            status: "pending",
          });
        }
        if (batch.length > 0) batches.push(batch);
      }
      // Tools-channel calls absent from the replayed messages (message deltas
      // are live-only in the durable event log) keep their order as single
      // steps, so older threads degrade to the flat list.
      for (const [id, tc] of fromTools) {
        if (!seen.has(id)) batches.push([tc]);
      }
      return batches;
    }, [assembled, messages]);

    const nestedToolCallCount = useMemo(
      () => nestedBatches.reduce((count, batch) => count + batch.length, 0),
      [nestedBatches]
    );

    const subAgent: SubAgent = useMemo(
      () => ({
        id: snapshot.id,
        name: taskToolCall.name,
        subAgentName: snapshot.name,
        input: taskToolCall.args,
        output:
          snapshot.output != null
            ? { result: toResultString(unwrapToolPayload(snapshot.output)) ?? "" }
            : taskToolCall.result
              ? { result: taskToolCall.result }
              : undefined,
        status: mapSnapshotStatus(snapshot.status),
      }),
      [snapshot, taskToolCall]
    );

    // Sub-second spans are noise, and history-reseeded snapshots carry
    // near-equal hydration timestamps — suppress rather than show "<1s".
    const durationLabel =
      snapshot.completedAt && snapshot.completedAt.getTime() - snapshot.startedAt.getTime() >= 1000
        ? formatDuration(snapshot.startedAt, snapshot.completedAt)
        : null;

    return (
      <div className={cardClassName}>
        <SubagentHeader
          name={subAgent.subAgentName}
          status={subAgent.status}
          toolCount={nestedToolCallCount}
          durationLabel={durationLabel}
          isExpanded={isExpanded}
          onToggle={toggle}
        />
        <Collapse isExpanded={isExpanded}>
          <div className="border-t border-border px-4 py-4">
            <h4 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
              Input
            </h4>
            <div className="mb-4 rounded-xl border border-border bg-background/50 p-3">
              <MarkdownContent content={extractSubAgentContent(subAgent.input)} />
            </div>
            {nestedBatches.length > 0 && (
              <>
                <h4 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                  Activity
                </h4>
                <div className="relative mb-4 before:absolute before:top-2 before:bottom-2 before:left-[12px] before:w-px before:bg-border2">
                  <ToolCallBatchList batches={nestedBatches} />
                </div>
              </>
            )}
            {subAgent.output && (
              <>
                <h4 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                  Output
                </h4>
                <div className="rounded-xl border border-border bg-background/50 p-3">
                  <MarkdownContent content={extractSubAgentContent(subAgent.output)} />
                </div>
              </>
            )}
          </div>
        </Collapse>
      </div>
    );
  }
);

SubagentCard.displayName = "SubagentCard";
