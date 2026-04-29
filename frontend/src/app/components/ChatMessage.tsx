"use client";

import React, { useMemo, useState, useCallback } from "react";
import { SubAgentIndicator } from "@/app/components/SubAgentIndicator";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import type { SubAgent, ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { Message } from "@langchain/langgraph-sdk";
import { extractSubAgentContent, extractStringFromMessageContent } from "@/app/utils/utils";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: Message;
  toolCalls: ToolCall[];
  isLoading?: boolean;
  actionRequestsMap?: Map<string, ActionRequest>;
  reviewConfigsMap?: Map<string, ReviewConfig>;
  ui?: any[];
  stream?: any;
  onResumeInterrupt?: (value: any) => void;
  graphId?: string;
}

export const ChatMessage = React.memo<ChatMessageProps>(
  ({
    message,
    toolCalls,
    isLoading,
    actionRequestsMap,
    reviewConfigsMap,
    ui,
    stream,
    onResumeInterrupt,
    graphId,
  }) => {
    const isUser = message.type === "human";
    const messageContent = extractStringFromMessageContent(message);
    const hasContent = messageContent && messageContent.trim() !== "";
    const hasToolCalls = toolCalls.length > 0;

    // Subscribe to the SDK's per-subagent state so the box stays populated
    // after the run reconciles with thread history (which only persists the
    // parent `task` tool call). `stream.subagents` is a Map kept up to date
    // by SubagentManager for the lifetime of the React state.
    const sdkSubagents: any[] = useMemo(() => {
      if (!stream?.getSubagentsByMessage || !message.id) return [];
      try {
        return stream.getSubagentsByMessage(message.id) ?? [];
      } catch {
        return [];
      }
    }, [stream, message.id, stream?.subagents]);

    const subAgents = useMemo(() => {
      const sdkById = new Map<string, any>(sdkSubagents.map((s) => [s.id, s]));
      const mapStatus = (
        sdkStatus: string | undefined,
        fallback: ToolCall["status"]
      ): SubAgent["status"] => {
        switch (sdkStatus) {
          case "running":
            return "active";
          case "complete":
            return "completed";
          case "pending":
            return "pending";
          case "error":
            return "error";
          default:
            if (fallback === "completed") return "completed";
            if (fallback === "error") return "error";
            return "pending";
        }
      };
      const toResultString = (result: unknown): string | undefined => {
        if (result == null) return undefined;
        if (typeof result === "string") return result;
        if (Array.isArray(result)) {
          return result
            .map((block) => {
              if (typeof block === "string") return block;
              if (block && typeof block === "object" && "text" in block) {
                return String((block as { text: unknown }).text ?? "");
              }
              try {
                return JSON.stringify(block);
              } catch {
                return String(block);
              }
            })
            .join("");
        }
        try {
          return JSON.stringify(result);
        } catch {
          return String(result);
        }
      };
      return toolCalls
        .filter((toolCall: ToolCall) => {
          return (
            toolCall.name === "task" &&
            toolCall.args["subagent_type"] &&
            toolCall.args["subagent_type"] !== "" &&
            toolCall.args["subagent_type"] !== null
          );
        })
        .map((toolCall: ToolCall) => {
          const subagentType = (toolCall.args as Record<string, unknown>)[
            "subagent_type"
          ] as string;
          const sdk = sdkById.get(toolCall.id);

          // The SDK exposes nested tool calls as `ToolCallWithResult` objects:
          // `{ id, call: { name, args, id }, result: ToolMessage|undefined, state }`.
          // Map them into our local flat ToolCall shape and skip nested `task`
          // invocations (we don't recursively render sub-subagents inside this
          // box).
          const nestedToolCalls: ToolCall[] | undefined = sdk?.toolCalls
            ? (sdk.toolCalls as Array<any>)
                .filter((tc) => tc?.call?.name && tc.call.name !== "task")
                .map((tc) => {
                  const call = tc.call ?? {};
                  const resultStr = toResultString(tc.result?.content);
                  const state = tc.state ?? (resultStr !== undefined ? "completed" : "pending");
                  const status: ToolCall["status"] =
                    state === "error" ? "error" : state === "completed" ? "completed" : "pending";
                  return {
                    id:
                      tc.id ||
                      call.id ||
                      `${toolCall.id}-${call.name}-${Math.random().toString(36).slice(2)}`,
                    name: call.name,
                    args: (call.args ?? {}) as Record<string, unknown>,
                    result: resultStr,
                    status,
                  } as ToolCall;
                })
            : undefined;

          return {
            id: toolCall.id,
            name: toolCall.name,
            subAgentName: subagentType,
            input: toolCall.args,
            output: toolCall.result ? { result: toolCall.result } : undefined,
            status: mapStatus(sdk?.status, toolCall.status),
            toolCalls: nestedToolCalls,
          } as SubAgent;
        });
    }, [toolCalls, sdkSubagents]);

    const [expandedSubAgents, setExpandedSubAgents] = useState<Record<string, boolean>>({});
    const isSubAgentExpanded = useCallback(
      (id: string) => expandedSubAgents[id] ?? true,
      [expandedSubAgents]
    );
    const toggleSubAgent = useCallback((id: string) => {
      setExpandedSubAgents((prev) => ({
        ...prev,
        [id]: prev[id] === undefined ? false : !prev[id],
      }));
    }, []);

    return (
      <div className={cn("flex w-full max-w-full overflow-x-hidden", isUser && "flex-row-reverse")}>
        <div className={cn("min-w-0 max-w-full", isUser ? "max-w-[70%]" : "w-full")}>
          {hasContent && (
            <div className={cn("relative flex items-end gap-0")}>
              <div
                className={cn(
                  "mt-4 overflow-hidden break-words text-sm font-normal leading-[150%]",
                  isUser
                    ? "border-primary/15 rounded-2xl rounded-br-md border px-4 py-2.5 text-foreground shadow-sm"
                    : "text-foreground"
                )}
                style={isUser ? { backgroundColor: "var(--color-user-message-bg)" } : undefined}
              >
                {isUser ? (
                  <p className="m-0 whitespace-pre-wrap break-words text-sm leading-relaxed">
                    {messageContent}
                  </p>
                ) : hasContent ? (
                  <MarkdownContent content={messageContent} />
                ) : null}
              </div>
            </div>
          )}
          {hasToolCalls && (
            <div className="mt-4 flex w-full flex-col gap-2">
              {toolCalls.map((toolCall: ToolCall) => {
                if (toolCall.name === "task") return null;
                const toolCallGenUiComponent = ui?.find(
                  (u) => u.metadata?.tool_call_id === toolCall.id
                );
                const actionRequest = actionRequestsMap?.get(toolCall.name);
                const reviewConfig = reviewConfigsMap?.get(toolCall.name);
                return (
                  <ToolCallBox
                    key={toolCall.id}
                    toolCall={toolCall}
                    uiComponent={toolCallGenUiComponent}
                    stream={stream}
                    graphId={graphId}
                    actionRequest={actionRequest}
                    reviewConfig={reviewConfig}
                    onResume={onResumeInterrupt}
                    isLoading={isLoading}
                  />
                );
              })}
            </div>
          )}
          {!isUser && subAgents.length > 0 && (
            <div className="mt-4 flex w-fit max-w-full flex-col gap-4">
              {subAgents.map((subAgent) => (
                <div key={subAgent.id} className="flex w-full flex-col gap-3">
                  <div className="flex items-end gap-2">
                    <div className="w-[calc(100%-100px)]">
                      <SubAgentIndicator
                        subAgent={subAgent}
                        onClick={() => toggleSubAgent(subAgent.id)}
                        isExpanded={isSubAgentExpanded(subAgent.id)}
                      />
                    </div>
                  </div>
                  {isSubAgentExpanded(subAgent.id) && (
                    <div className="w-full max-w-full">
                      <div className="rounded-2xl border border-border bg-surface-raised p-4 shadow-sm">
                        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          Input
                        </h4>
                        <div className="mb-4 rounded-xl border border-border bg-background/50 p-3">
                          <MarkdownContent content={extractSubAgentContent(subAgent.input)} />
                        </div>
                        {subAgent.toolCalls && subAgent.toolCalls.length > 0 && (
                          <>
                            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                              Activity
                            </h4>
                            <div className="mb-4 flex flex-col gap-2 border-l border-border pl-3">
                              {subAgent.toolCalls.map((tc) => (
                                <ToolCallBox
                                  key={tc.id}
                                  toolCall={tc}
                                  stream={stream}
                                  graphId={graphId}
                                />
                              ))}
                            </div>
                          </>
                        )}
                        {subAgent.output && (
                          <>
                            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
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
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }
);

ChatMessage.displayName = "ChatMessage";
