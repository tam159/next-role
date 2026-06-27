"use client";

import React, { useRef, useCallback, useMemo, useEffect, useState, FormEvent } from "react";
import {
  Square,
  ArrowUp,
  Paperclip,
  Loader2,
  FileText,
  Search,
  ClipboardList,
  GraduationCap,
} from "lucide-react";
import { toast } from "sonner";
import { ChatMessage } from "@/app/components/ChatMessage";
import { LogoMark } from "@/app/components/LogoMark";
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
import { CAREER_AGENT_UPLOAD_DIR, uploadAgentFiles } from "@/app/lib/uploadFiles";
import { useStickToBottom } from "use-stick-to-bottom";
import { cn } from "@/lib/utils";

interface ChatInterfaceProps {
  assistant: Assistant | null;
}

// Empty-state suggestion chips. Clicking fills the composer.
const SUGGESTIONS = [
  {
    label: "Research a company",
    icon: Search,
    prompt: "Research a company and role I'm interviewing for, and summarize what they look for.",
  },
  {
    label: "Tailor my resume",
    icon: FileText,
    prompt: "Tailor my resume to a specific job description.",
  },
  {
    label: "Prepare for an interview",
    icon: GraduationCap,
    prompt: "Prepare me for the interview — build round-by-round prep with STAR stories.",
  },
  {
    label: "Build a battlecard",
    icon: ClipboardList,
    prompt: "Build a one-page interview battlecard for my upcoming round.",
  },
];

export const ChatInterface = React.memo<ChatInterfaceProps>(({ assistant }) => {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const attachInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

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
    refreshFiles,
    appendUploadNote,
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

  const fillSuggestion = useCallback(
    (prompt: string) => {
      setInput(prompt);
      requestAnimationFrame(() => {
        const t = textareaRef.current;
        if (!t) return;
        t.focus();
        const end = t.value.length;
        t.setSelectionRange(end, end);
      });
    },
    [setInput]
  );

  // Attach (paperclip) — reuses the same upload path as Workspace > Files.
  const handleAttach = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files;
      if (!list || list.length === 0) return;
      const picked = Array.from(list);
      e.target.value = "";
      setUploading(true);
      try {
        const res = await uploadAgentFiles({ files: picked, targetDir: CAREER_AGENT_UPLOAD_DIR });
        if (res.uploaded.length > 0) {
          toast.success(
            `Uploaded ${res.uploaded.length} file${res.uploaded.length > 1 ? "s" : ""}`
          );
          appendUploadNote(
            res.uploaded.map((u) => u.path.split("/").pop()).filter((n): n is string => !!n)
          );
        }
        for (const err of res.errors) toast.error(`${err.name}: ${err.reason}`);
        await refreshFiles?.();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [appendUploadNote, refreshFiles]
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

  const isEmpty = !isThreadLoading && processedMessages.length === 0;
  const canSend = !submitDisabled && input.trim().length > 0;

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-canvas">
      <div className="flex-1 overflow-x-hidden overflow-y-auto overscroll-contain" ref={scrollRef}>
        <div ref={contentRef}>
          {isThreadLoading ? (
            <div className="flex items-center justify-center p-8">
              <p className="text-secondary">Loading…</p>
            </div>
          ) : isEmpty ? (
            <div className="flex min-h-[68vh] flex-col items-center justify-center px-7 py-10 text-center">
              <div
                className="mb-6"
                style={{
                  filter:
                    "drop-shadow(0 10px 28px color-mix(in srgb, var(--brand-accent) 32%, transparent))",
                }}
              >
                <LogoMark size={56} />
              </div>
              <h1 className="font-serif text-[40px] leading-[1.08] font-medium tracking-[-0.01em] text-primary">
                Land your <em className="text-brand-accent-text italic">next role</em>, faster.
              </h1>
              <p className="mx-auto mt-4 max-w-[480px] text-[15.5px] leading-relaxed text-secondary">
                Drop in your resume and a job post. NextRole tailors your application, researches
                the company, and preps you for every round — all in one workspace.
              </p>
              <div className="mt-7 flex max-w-[560px] flex-wrap justify-center gap-2.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    onClick={() => fillSuggestion(s.prompt)}
                    className="flex items-center gap-2 rounded-[11px] border border-primary bg-surface-raised px-3.5 py-2.5 text-[13.5px] font-medium text-primary shadow-sm transition-all hover:-translate-y-px hover:border-brand-strong"
                  >
                    <s.icon className="size-4 text-brand-accent" />
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-[760px] px-6 pt-6 pb-8">
              {processedMessages.map((data, index) => {
                const isLastMessage = index === processedMessages.length - 1;
                return (
                  <ChatMessage
                    key={data.message.id}
                    message={data.message}
                    toolCalls={data.toolCalls}
                    isLoading={isLoading}
                    showAvatar={data.showAvatar}
                    actionRequestsMap={isLastMessage ? actionRequestsMap : undefined}
                    reviewConfigsMap={isLastMessage ? reviewConfigsMap : undefined}
                    stream={stream}
                    onResumeInterrupt={resumeInterrupt}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 bg-linear-to-t from-canvas via-canvas to-transparent px-4 pt-4 pb-5">
        <div className="mx-auto w-full max-w-[760px]">
          {isLoading && (
            <div className="mb-2 flex items-center gap-2 px-2 text-xs text-secondary">
              <span className="size-1.5 animate-pulse rounded-full bg-brand-accent" />
              NextRole is working…
            </div>
          )}
          <div className="flex flex-col overflow-hidden rounded-[18px] border border-primary bg-surface-raised shadow-lg shadow-black/5 transition-colors focus-within:border-brand-strong focus-within:ring-2 focus-within:ring-brand-accent/30">
            <form onSubmit={handleSubmit} className="flex flex-col">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  requestAnimationFrame(resizeTextarea);
                }}
                onKeyDown={handleKeyDown}
                placeholder="Message NextRole — paste a job link, or describe the role…"
                className="block w-full resize-none border-0 bg-transparent px-5 pt-4 pb-3 text-[15px] leading-7 text-primary outline-hidden placeholder:text-tertiary"
                rows={2}
              />
              <div className="flex items-center justify-between gap-2 px-3 pb-3">
                <div className="flex min-w-0 items-center gap-1.5">
                  <input
                    ref={attachInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.doc,.docx,.txt,.md"
                    className="hidden"
                    onChange={handleAttach}
                  />
                  <button
                    type="button"
                    title="Attach a file"
                    onClick={() => attachInputRef.current?.click()}
                    disabled={uploading || submitDisabled}
                    className="grid size-9 shrink-0 place-items-center rounded-full text-tertiary transition-colors hover:bg-surface3 hover:text-primary disabled:opacity-50"
                  >
                    {uploading ? (
                      <Loader2 size={16} className="animate-spin" />
                    ) : (
                      <Paperclip size={16} />
                    )}
                  </button>
                  <span className="truncate text-xs text-tertiary">
                    Enter to send · Shift+Enter for newline
                  </span>
                </div>
                <button
                  type={isLoading ? "button" : "submit"}
                  onClick={isLoading ? stopStream : handleSubmit}
                  disabled={!isLoading && !canSend}
                  aria-label={isLoading ? "Stop" : "Send"}
                  className={cn(
                    "grid size-9 shrink-0 place-items-center rounded-full transition-colors",
                    isLoading
                      ? "bg-destructive text-white hover:bg-destructive/90"
                      : canSend
                        ? "bg-brand-accent text-on-accent hover:bg-brand-accent-hover"
                        : "bg-border2 text-tertiary"
                  )}
                >
                  {isLoading ? <Square size={14} /> : <ArrowUp size={18} />}
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
});

ChatInterface.displayName = "ChatInterface";
