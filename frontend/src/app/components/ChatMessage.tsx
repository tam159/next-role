"use client";

import React, { useMemo } from "react";
import { SubAgentIndicator } from "@/app/components/SubAgentIndicator";
import { SubagentCard } from "@/app/components/SubagentCard";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import type { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import type { BaseMessage } from "@langchain/core/messages";
import type { AnyStream } from "@langchain/react";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: BaseMessage;
  toolCalls: ToolCall[];
  isLoading?: boolean;
  actionRequestsMap?: Map<string, ActionRequest>;
  reviewConfigsMap?: Map<string, ReviewConfig>;
  stream: AnyStream;
  onResumeInterrupt?: (value: unknown) => void;
}

export const ChatMessage = React.memo<ChatMessageProps>(
  ({
    message,
    toolCalls,
    isLoading,
    actionRequestsMap,
    reviewConfigsMap,
    stream,
    onResumeInterrupt,
  }) => {
    const isUser = message.type === "human";
    const messageContent = extractStringFromMessageContent(message);
    const hasContent = messageContent && messageContent.trim() !== "";
    const hasToolCalls = toolCalls.length > 0;

    // A `task` tool call's id doubles as the subagent discovery key:
    // `stream.subagents` is keyed by the spawning tool-call id.
    const taskToolCalls = useMemo(
      () =>
        toolCalls.filter(
          (toolCall) =>
            toolCall.name === "task" &&
            toolCall.args["subagent_type"] &&
            toolCall.args["subagent_type"] !== "" &&
            toolCall.args["subagent_type"] !== null
        ),
      [toolCalls]
    );

    return (
      <div className={cn("flex w-full max-w-full overflow-x-hidden", isUser && "flex-row-reverse")}>
        <div className={cn("max-w-full min-w-0", isUser ? "max-w-[70%]" : "w-full")}>
          {hasContent && (
            <div className={cn("relative flex items-end gap-0")}>
              <div
                className={cn(
                  "mt-4 overflow-hidden text-sm leading-[150%] font-normal wrap-break-word",
                  isUser
                    ? "rounded-2xl rounded-br-md border border-primary/15 px-4 py-2.5 text-foreground shadow-xs"
                    : "text-foreground"
                )}
                style={isUser ? { backgroundColor: "var(--color-user-message-bg)" } : undefined}
              >
                {isUser ? (
                  <p className="m-0 text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
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
                const actionRequest = actionRequestsMap?.get(toolCall.name);
                const reviewConfig = reviewConfigsMap?.get(toolCall.name);
                return (
                  <ToolCallBox
                    key={toolCall.id}
                    toolCall={toolCall}
                    actionRequest={actionRequest}
                    reviewConfig={reviewConfig}
                    onResume={onResumeInterrupt}
                    isLoading={isLoading}
                  />
                );
              })}
            </div>
          )}
          {!isUser && taskToolCalls.length > 0 && (
            <div className="mt-4 flex w-fit max-w-full flex-col gap-4">
              {taskToolCalls.map((toolCall) => {
                const snapshot = stream.subagents.get(toolCall.id);
                if (!snapshot) {
                  // Discovery hasn't landed yet (the task call's args are
                  // still streaming): show a static queued indicator until
                  // the tools-channel event creates the snapshot.
                  return (
                    <SubAgentIndicator
                      key={toolCall.id}
                      subAgent={{
                        id: toolCall.id,
                        name: toolCall.name,
                        subAgentName: String(toolCall.args["subagent_type"]),
                        input: toolCall.args,
                        status: "pending",
                      }}
                      onClick={() => {}}
                    />
                  );
                }
                return (
                  <SubagentCard
                    key={toolCall.id}
                    stream={stream}
                    snapshot={snapshot}
                    taskToolCall={toolCall}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }
);

ChatMessage.displayName = "ChatMessage";
