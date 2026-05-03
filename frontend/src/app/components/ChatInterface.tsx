"use client";

import React, { useRef, useCallback, useMemo, useEffect, FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Square, ArrowUp } from "lucide-react";
import { ChatMessage } from "@/app/components/ChatMessage";
import type { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { Assistant, Message } from "@langchain/langgraph-sdk";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { useChatContext } from "@/providers/ChatProvider";
import { useStickToBottom } from "use-stick-to-bottom";

interface ChatInterfaceProps {
  assistant: Assistant | null;
}

export const ChatInterface = React.memo<ChatInterfaceProps>(({ assistant }) => {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const { scrollRef, contentRef } = useStickToBottom();

  const {
    stream,
    messages,
    ui,
    isLoading,
    isThreadLoading,
    interrupt,
    sendMessage,
    stopStream,
    resumeInterrupt,
    input,
    setInput,
    focusComposerNonce,
  } = useChatContext();

  const submitDisabled = isLoading || !assistant;

  // Focus the textarea (with cursor at end) whenever a sibling surface — e.g.
  // Workspace > Files Upload — appended an "Uploaded: ..." note. Skip the
  // initial mount nonce==0.
  useEffect(() => {
    if (focusComposerNonce === 0) return;
    const t = textareaRef.current;
    if (!t) return;
    t.focus();
    const end = t.value.length;
    t.setSelectionRange(end, end);
  }, [focusComposerNonce]);

  const resizeTextarea = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const style = window.getComputedStyle(textarea);
    const lineHeight = parseFloat(style.lineHeight) || 28;
    const paddingTop = parseFloat(style.paddingTop) || 0;
    const paddingBottom = parseFloat(style.paddingBottom) || 0;
    const verticalPadding = paddingTop + paddingBottom;
    const minHeight = lineHeight * 2 + verticalPadding;
    const maxHeight = lineHeight * 5 + verticalPadding;

    textarea.style.height = `${minHeight}px`;

    const nextHeight = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > nextHeight + 1 ? "auto" : "hidden";
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [input, resizeTextarea]);

  const handleSubmit = useCallback(
    (e?: FormEvent) => {
      if (e) {
        e.preventDefault();
      }
      const messageText = input.trim();
      if (!messageText || isLoading || submitDisabled) return;
      sendMessage(messageText);
      setInput("");
    },
    [input, isLoading, sendMessage, setInput, submitDisabled]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (submitDisabled) return;
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit, submitDisabled]
  );

  // TODO: can we make this part of the hook?
  const processedMessages = useMemo(() => {
    /*
     1. Loop through all messages
     2. For each AI message, add the AI message, and any tool calls to the messageMap
     3. For each tool message, find the corresponding tool call in the messageMap and update the status and output
    */
    const messageMap = new Map<string, { message: Message; toolCalls: ToolCall[] }>();
    messages.forEach((message: Message) => {
      if (message.type === "ai") {
        const toolCallsInMessage: Array<{
          id?: string;
          function?: { name?: string; arguments?: unknown };
          name?: string;
          type?: string;
          args?: unknown;
          input?: unknown;
        }> = [];
        if (
          message.additional_kwargs?.tool_calls &&
          Array.isArray(message.additional_kwargs.tool_calls)
        ) {
          toolCallsInMessage.push(...message.additional_kwargs.tool_calls);
        } else if (message.tool_calls && Array.isArray(message.tool_calls)) {
          toolCallsInMessage.push(
            ...message.tool_calls.filter((toolCall: { name?: string }) => toolCall.name !== "")
          );
        } else if (Array.isArray(message.content)) {
          const toolUseBlocks = message.content.filter(
            (block: { type?: string }) => block.type === "tool_use"
          );
          toolCallsInMessage.push(...toolUseBlocks);
        }
        const toolCallsWithStatus = toolCallsInMessage.map(
          (toolCall: {
            id?: string;
            function?: { name?: string; arguments?: unknown };
            name?: string;
            type?: string;
            args?: unknown;
            input?: unknown;
          }) => {
            const name = toolCall.function?.name || toolCall.name || toolCall.type || "unknown";
            const args = toolCall.function?.arguments || toolCall.args || toolCall.input || {};
            return {
              id: toolCall.id || `tool-${Math.random()}`,
              name,
              args,
              status: interrupt ? "interrupted" : ("pending" as const),
            } as ToolCall;
          }
        );
        messageMap.set(message.id!, {
          message,
          toolCalls: toolCallsWithStatus,
        });
      } else if (message.type === "tool") {
        const toolCallId = message.tool_call_id;
        if (!toolCallId) {
          return;
        }
        for (const [, data] of messageMap.entries()) {
          const toolCallIndex = data.toolCalls.findIndex((tc: ToolCall) => tc.id === toolCallId);
          if (toolCallIndex === -1) {
            continue;
          }
          data.toolCalls[toolCallIndex] = {
            ...data.toolCalls[toolCallIndex],
            status: "completed" as const,
            result: extractStringFromMessageContent(message),
          };
          break;
        }
      } else if (message.type === "human") {
        messageMap.set(message.id!, {
          message,
          toolCalls: [],
        });
      }
    });
    const processedArray = Array.from(messageMap.values());
    return processedArray.map((data, index) => {
      const prevMessage = index > 0 ? processedArray[index - 1].message : null;
      return {
        ...data,
        showAvatar: data.message.type !== prevMessage?.type,
      };
    });
  }, [messages, interrupt]);

  // Parse out any action requests or review configs from the interrupt
  const actionRequestsMap: Map<string, ActionRequest> | null = useMemo(() => {
    const actionRequests = interrupt?.value && (interrupt.value as any)["action_requests"];
    if (!actionRequests) return new Map<string, ActionRequest>();
    return new Map(actionRequests.map((ar: ActionRequest) => [ar.name, ar]));
  }, [interrupt]);

  const reviewConfigsMap: Map<string, ReviewConfig> | null = useMemo(() => {
    const reviewConfigs = interrupt?.value && (interrupt.value as any)["review_configs"];
    if (!reviewConfigs) return new Map<string, ReviewConfig>();
    return new Map(reviewConfigs.map((rc: ReviewConfig) => [rc.actionName, rc]));
  }, [interrupt]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-canvas">
      <div className="flex-1 overflow-y-auto overflow-x-hidden overscroll-contain" ref={scrollRef}>
        <div className="mx-auto w-full max-w-[1024px] px-6 pb-8 pt-5" ref={contentRef}>
          {isThreadLoading ? (
            <div className="flex items-center justify-center p-8">
              <p className="text-muted-foreground">Loading...</p>
            </div>
          ) : (
            <>
              {processedMessages.map((data, index) => {
                const messageUi = ui?.filter(
                  (u: any) => u.metadata?.message_id === data.message.id
                );
                const isLastMessage = index === processedMessages.length - 1;
                return (
                  <ChatMessage
                    key={data.message.id}
                    message={data.message}
                    toolCalls={data.toolCalls}
                    isLoading={isLoading}
                    actionRequestsMap={isLastMessage ? actionRequestsMap : undefined}
                    reviewConfigsMap={isLastMessage ? reviewConfigsMap : undefined}
                    ui={messageUi}
                    stream={stream}
                    onResumeInterrupt={resumeInterrupt}
                    graphId={assistant?.graph_id}
                  />
                );
              })}
            </>
          )}
        </div>
      </div>

      <div className="flex-shrink-0 bg-gradient-to-t from-canvas via-canvas to-transparent px-4 pb-5 pt-4">
        <div className="mx-auto flex w-full max-w-[1024px] flex-shrink-0 flex-col overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-lg shadow-black/5">
          <form onSubmit={handleSubmit} className="flex flex-col">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                requestAnimationFrame(resizeTextarea);
              }}
              onKeyDown={handleKeyDown}
              placeholder={isLoading ? "Running..." : "Write your message..."}
              className="font-inherit block w-full resize-none border-0 bg-transparent px-5 pb-3 pt-4 text-sm leading-7 text-foreground outline-none placeholder:text-muted-foreground"
              rows={2}
            />
            <div className="flex items-center justify-between gap-2 border-t border-border/70 px-3 py-3">
              <p className="pl-2 text-xs text-muted-foreground">
                Enter to send, Shift+Enter for a new line
              </p>
              <div className="flex justify-end gap-2">
                <Button
                  type={isLoading ? "button" : "submit"}
                  variant={isLoading ? "destructive" : "default"}
                  onClick={isLoading ? stopStream : handleSubmit}
                  disabled={!isLoading && (submitDisabled || !input.trim())}
                  className="rounded-full px-4"
                >
                  {isLoading ? (
                    <>
                      <Square size={14} />
                      <span>Stop</span>
                    </>
                  ) : (
                    <>
                      <ArrowUp size={18} />
                      <span>Send</span>
                    </>
                  )}
                </Button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
});

ChatInterface.displayName = "ChatInterface";
