"use client";

import React, { useMemo } from "react";
import { QueuedSubagentCard, SubagentCard } from "@/app/components/SubagentCard";
import { ToolCallGroup } from "@/app/components/ToolCallGroup";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { LogoMark } from "@/app/components/LogoMark";
import type { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import type { BaseMessage } from "@langchain/core/messages";
import type { AnyStream } from "@langchain/react";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: BaseMessage;
  toolCalls: ToolCall[];
  /**
   * The merged run of regular tool calls headed by this message (batched per
   * issuing message), or null when this message's calls render under an
   * earlier head. Computed in ChatInterface, where runs span messages.
   */
  toolBatches?: ToolCall[][] | null;
  isOpenEndedGroup?: boolean;
  isLoading?: boolean;
  showAvatar?: boolean;
  actionRequestsMap?: Map<string, ActionRequest>;
  reviewConfigsMap?: Map<string, ReviewConfig>;
  stream: AnyStream;
  onResumeInterrupt?: (value: unknown) => void;
}

export const ChatMessage = React.memo<ChatMessageProps>(
  ({
    message,
    toolCalls,
    toolBatches,
    isOpenEndedGroup,
    isLoading,
    showAvatar = true,
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

    const isThinking =
      !isUser && !hasContent && !hasToolCalls && taskToolCalls.length === 0 && isLoading;

    return (
      <div
        className={cn(
          "flex w-full max-w-full items-start overflow-x-hidden",
          isUser ? "flex-row-reverse" : "gap-3"
        )}
      >
        {!isUser &&
          (showAvatar ? (
            <LogoMark size={28} className="mt-4 shrink-0" />
          ) : (
            <div className="w-7 shrink-0" aria-hidden />
          ))}
        <div className={cn("min-w-0", isUser ? "max-w-[78%]" : "flex-1")}>
          {isThinking && (
            <div className="mt-4 flex items-center gap-2.5 text-sm text-secondary">
              <span className="flex gap-1">
                <span className="size-1.5 animate-pulse rounded-full bg-brand-accent [animation-duration:1s]" />
                <span className="size-1.5 animate-pulse rounded-full bg-brand-accent [animation-delay:0.2s] [animation-duration:1s]" />
                <span className="size-1.5 animate-pulse rounded-full bg-brand-accent [animation-delay:0.4s] [animation-duration:1s]" />
              </span>
              Working through your request
            </div>
          )}
          {hasContent && (
            <div className={cn("relative flex items-end gap-0")}>
              <div
                className={cn(
                  "mt-4 overflow-hidden text-[15px] leading-[1.6] font-normal wrap-break-word",
                  isUser
                    ? "rounded-2xl rounded-br-[5px] border border-primary/15 px-4 py-2.5 text-primary shadow-xs"
                    : "pt-1 text-primary"
                )}
                style={isUser ? { backgroundColor: "var(--color-user-message-bg)" } : undefined}
              >
                {isUser ? (
                  <p className="m-0 text-[15px] leading-relaxed wrap-break-word whitespace-pre-wrap">
                    {messageContent}
                  </p>
                ) : hasContent ? (
                  <MarkdownContent content={messageContent} />
                ) : null}
              </div>
            </div>
          )}
          {toolBatches && toolBatches.length > 0 && (
            <ToolCallGroup
              batches={toolBatches}
              isLoading={isLoading}
              isOpenEnded={isOpenEndedGroup}
              actionRequestsMap={actionRequestsMap}
              reviewConfigsMap={reviewConfigsMap}
              onResumeInterrupt={onResumeInterrupt}
            />
          )}
          {!isUser && taskToolCalls.length > 0 && (
            <div className="mt-4 flex w-full flex-col gap-4">
              {taskToolCalls.map((toolCall) => {
                const snapshot = stream.subagents.get(toolCall.id);
                if (!snapshot) {
                  // Discovery hasn't landed yet (the task call's args are
                  // still streaming): show a header-only queued card until
                  // the tools-channel event creates the snapshot.
                  return (
                    <QueuedSubagentCard
                      key={toolCall.id}
                      name={String(toolCall.args["subagent_type"])}
                    />
                  );
                }
                return (
                  <SubagentCard
                    key={toolCall.id}
                    stream={stream}
                    snapshot={snapshot}
                    taskToolCall={toolCall}
                    isLoading={isLoading}
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
