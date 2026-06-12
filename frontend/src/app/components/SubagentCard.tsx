"use client";

import React, { useMemo, useState } from "react";
import {
  useMessages,
  useToolCalls,
  type AnyStream,
  type SubagentDiscoverySnapshot,
} from "@langchain/react";
import { type AIMessageChunk, isAIMessage } from "@langchain/core/messages";
import { SubAgentIndicator } from "@/app/components/SubAgentIndicator";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import type { SubAgent, ToolCall } from "@/app/types/types";
import {
  extractSubAgentContent,
  parsePartialArgs,
  toResultString,
  unwrapToolPayload,
} from "@/app/utils/utils";

function mapSnapshotStatus(status: SubagentDiscoverySnapshot["status"]): SubAgent["status"] {
  if (status === "running") return "active";
  if (status === "error") return "error";
  return "completed";
}

interface SubagentCardProps {
  stream: AnyStream;
  snapshot: SubagentDiscoverySnapshot;
  taskToolCall: ToolCall;
}

/**
 * One subagent's progress card. Mounting subscribes to the subagent's
 * namespaced projections (ref-counted; shared with any other consumer):
 *
 *  - `tools` — authoritative call lifecycle (`tool-started/finished/error`)
 *    with parsed args and outputs. No arg deltas on this channel.
 *  - `messages` — the subagent's own LLM stream; a call whose args are
 *    still streaming exists only as `tool_call_chunks` here, so this is
 *    what makes nested tool args grow token-by-token live.
 */
export const SubagentCard = React.memo<SubagentCardProps>(({ stream, snapshot, taskToolCall }) => {
  const assembled = useToolCalls(stream, snapshot);
  const messages = useMessages(stream, snapshot);
  const [isExpanded, setIsExpanded] = useState(true);

  const nestedToolCalls: ToolCall[] = useMemo(() => {
    const fromTools: ToolCall[] = assembled
      .filter((tc) => tc.name !== "task")
      .map((tc) => ({
        id: tc.id,
        name: tc.name,
        args: (tc.args ?? {}) as Record<string, unknown>,
        result: toResultString(unwrapToolPayload(tc.output)),
        status:
          tc.status === "error" ? "error" : tc.status === "finished" ? "completed" : "pending",
      }));
    const startedIds = new Set(assembled.map((tc) => tc.id));
    const streaming: ToolCall[] = [];
    for (const message of messages) {
      if (!isAIMessage(message)) continue;
      for (const chunk of (message as AIMessageChunk).tool_call_chunks ?? []) {
        if (!chunk.id || !chunk.name || chunk.name === "task") continue;
        if (startedIds.has(chunk.id)) continue;
        streaming.push({
          id: chunk.id,
          name: chunk.name,
          args: parsePartialArgs(chunk.args),
          status: "pending",
        });
      }
    }
    return [...fromTools, ...streaming];
  }, [assembled, messages]);

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

  return (
    <div className="flex w-full flex-col gap-3">
      <div className="flex items-end gap-2">
        <div className="w-[calc(100%-100px)]">
          <SubAgentIndicator
            subAgent={subAgent}
            onClick={() => setIsExpanded((prev) => !prev)}
            isExpanded={isExpanded}
          />
        </div>
      </div>
      {isExpanded && (
        <div className="w-full max-w-full">
          <div className="rounded-2xl border border-border bg-surface-raised p-4 shadow-xs">
            <h4 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
              Input
            </h4>
            <div className="mb-4 rounded-xl border border-border bg-background/50 p-3">
              <MarkdownContent content={extractSubAgentContent(subAgent.input)} />
            </div>
            {nestedToolCalls.length > 0 && (
              <>
                <h4 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                  Activity
                </h4>
                <div className="mb-4 flex flex-col gap-2 border-l border-border pl-3">
                  {nestedToolCalls.map((tc) => (
                    <ToolCallBox key={tc.id} toolCall={tc} />
                  ))}
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
        </div>
      )}
    </div>
  );
});

SubagentCard.displayName = "SubagentCard";
