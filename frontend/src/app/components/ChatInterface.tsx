"use client";

import React, { useRef, useCallback, useMemo, useEffect, FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Square, ArrowUp } from "lucide-react";
import { ChatMessage } from "@/app/components/ChatMessage";
import type { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { Assistant } from "@langchain/langgraph-sdk";
import {
  type AIMessageChunk,
  type BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";
import { extractStringFromMessageContent, parsePartialArgs } from "@/app/utils/utils";
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

  // Cache stable references for finalized ToolCall objects and per-message
  // toolCall arrays. While streaming, each token produces a new `messages`
  // reference, which previously rebuilt every ToolCall fresh and defeated the
  // React.memo on ChatMessage/ToolCallBox. We keep finalized entries cached by
  // id and only emit a new array reference for messages whose entries actually
  // changed — the streaming AI message gets a fresh reference so its tool box
  // re-renders and shows live tokens; all earlier messages stay stable.
  const toolCallCacheRef = useRef(new Map<string, ToolCall>());
  const toolCallArrayCacheRef = useRef(new Map<string, ToolCall[]>());

  // TODO: can we make this part of the hook?
  const processedMessages = useMemo(() => {
    /*
     1. Loop through all messages
     2. For each AI message, add the AI message, and any tool calls to the messageMap
     3. For each tool message, find the corresponding tool call in the messageMap and update the status and output
    */
    const messageMap = new Map<string, { message: BaseMessage; toolCalls: ToolCall[] }>();
    messages.forEach((message: BaseMessage) => {
      if (isAIMessage(message)) {
        // Completed calls live on `tool_calls` (parsed args); a call whose
        // args are still streaming only exists as a `tool_call_chunks` entry
        // (partial-JSON string args) until it finishes assembling.
        const status: ToolCall["status"] = interrupt ? "interrupted" : "pending";
        const done = (message.tool_calls ?? []).filter((tc) => tc.name !== "");
        const doneIds = new Set(done.map((tc) => tc.id));
        const chunks = (message as AIMessageChunk).tool_call_chunks ?? [];
        const streaming = chunks.filter((c) => c.id && c.name && !doneIds.has(c.id));
        // Pending tools: always fresh reference so the streaming box re-renders.
        // Cache hit happens later when the matching ToolMessage flips status.
        const toolCallsWithStatus: ToolCall[] = [
          ...done.map((tc) => ({
            id: tc.id ?? `tool-${Math.random()}`,
            name: tc.name,
            args: (tc.args ?? {}) as Record<string, unknown>,
            status,
          })),
          ...streaming.map((c) => ({
            id: c.id!,
            name: c.name!,
            args: parsePartialArgs(c.args),
            status,
          })),
        ];
        messageMap.set(message.id!, {
          message,
          toolCalls: toolCallsWithStatus,
        });
      } else if (isToolMessage(message)) {
        const toolCallId = message.tool_call_id;
        if (!toolCallId) {
          return;
        }
        for (const [, data] of messageMap.entries()) {
          const toolCallIndex = data.toolCalls.findIndex((tc: ToolCall) => tc.id === toolCallId);
          if (toolCallIndex === -1) {
            continue;
          }
          const result = extractStringFromMessageContent(message);
          const cached = toolCallCacheRef.current.get(toolCallId);
          if (cached && cached.status === "completed" && cached.result === result) {
            data.toolCalls[toolCallIndex] = cached;
          } else {
            const next: ToolCall = {
              ...data.toolCalls[toolCallIndex],
              status: "completed" as const,
              result,
            };
            toolCallCacheRef.current.set(toolCallId, next);
            data.toolCalls[toolCallIndex] = next;
          }
          break;
        }
      } else if (isHumanMessage(message)) {
        messageMap.set(message.id!, {
          message,
          toolCalls: [],
        });
      }
    });
    const processedArray = Array.from(messageMap.values());
    // Reuse the previous toolCalls array reference when none of the entries
    // changed identity, so React.memo on ChatMessage short-circuits for messages
    // that aren't actively streaming.
    const arrayCache = toolCallArrayCacheRef.current;
    const seenMessageIds = new Set<string>();
    const result = processedArray.map((data, index) => {
      const prevMessage = index > 0 ? processedArray[index - 1].message : null;
      const messageId = data.message.id;
      let toolCalls = data.toolCalls;
      if (messageId) {
        seenMessageIds.add(messageId);
        const cachedArray = arrayCache.get(messageId);
        const sameRefs =
          cachedArray &&
          cachedArray.length === toolCalls.length &&
          cachedArray.every((tc, i) => tc === toolCalls[i]);
        if (sameRefs) {
          toolCalls = cachedArray!;
        } else {
          arrayCache.set(messageId, toolCalls);
        }
      }
      return {
        message: data.message,
        toolCalls,
        showAvatar: data.message.type !== prevMessage?.type,
      };
    });
    // Drop entries for messages that no longer exist (e.g. thread switch).
    for (const id of arrayCache.keys()) {
      if (!seenMessageIds.has(id)) arrayCache.delete(id);
    }
    return result;
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
      <div className="flex-1 overflow-x-hidden overflow-y-auto overscroll-contain" ref={scrollRef}>
        <div className="mx-auto w-full max-w-[1024px] px-6 pt-5 pb-8" ref={contentRef}>
          {isThreadLoading ? (
            <div className="flex items-center justify-center p-8">
              <p className="text-muted-foreground">Loading...</p>
            </div>
          ) : (
            <>
              {processedMessages.map((data, index) => {
                const isLastMessage = index === processedMessages.length - 1;
                return (
                  <ChatMessage
                    key={data.message.id}
                    message={data.message}
                    toolCalls={data.toolCalls}
                    isLoading={isLoading}
                    actionRequestsMap={isLastMessage ? actionRequestsMap : undefined}
                    reviewConfigsMap={isLastMessage ? reviewConfigsMap : undefined}
                    stream={stream}
                    onResumeInterrupt={resumeInterrupt}
                  />
                );
              })}
            </>
          )}
        </div>
      </div>

      <div className="shrink-0 bg-linear-to-t from-canvas via-canvas to-transparent px-4 pt-4 pb-5">
        <div className="mx-auto flex w-full max-w-[1024px] shrink-0 flex-col overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-lg shadow-black/5">
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
              className="font-inherit block w-full resize-none border-0 bg-transparent px-5 pt-4 pb-3 text-sm leading-7 text-foreground outline-hidden placeholder:text-muted-foreground"
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
