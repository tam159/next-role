"use client";

import React, { useRef, useCallback, useMemo, useEffect, FormEvent } from "react";
import {
  Square,
  ArrowUp,
  Paperclip,
  Loader2,
  FileText,
  Search,
  ClipboardList,
  GraduationCap,
  Upload,
} from "lucide-react";
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
import { useFileUpload, useUploadDrop } from "@/app/hooks/useFileUpload";
import { useUploadCue } from "@/app/hooks/useUploadCue";
import { UPLOAD_ACCEPT } from "@/app/lib/uploadFiles";
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

// The composer's paperclip attach is hidden by default — uploads are handled in
// the Workspace (Files → Upload), which uses the same path. Flip to `true` to
// re-show the paperclip in the composer; the upload logic below stays wired.
const COMPOSER_ATTACH_ENABLED = false;

export const ChatInterface = React.memo<ChatInterfaceProps>(({ assistant }) => {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const attachInputRef = useRef<HTMLInputElement | null>(null);
  const { uploading, uploadFiles, onInputChange } = useFileUpload();
  const { dragActive, dropHandlers } = useUploadDrop(uploadFiles, uploading);
  const { showUploadCta } = useUploadCue();

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

  // Merge consecutive runs of regular (non-`task`) tool calls across AI
  // messages into one disclosure unit per run, batched by issuing message
  // (calls in one message ran in parallel). A run breaks on prose — which
  // renders above its own message's tool calls — on a subagent spawn — whose
  // card renders below them — and on human messages. The same identity-caching
  // discipline as processedMessages applies: a head keeps its previous batches
  // reference while no member batch changed, so React.memo holds downstream.
  const regularToolCallsCacheRef = useRef(
    new Map<string, { source: ToolCall[]; regular: ToolCall[] }>()
  );
  const toolBatchesCacheRef = useRef(new Map<string, ToolCall[][]>());

  const { toolBatchesByHead, openEndedHeadId } = useMemo(() => {
    const regularCache = regularToolCallsCacheRef.current;
    const batchesCache = toolBatchesCacheRef.current;
    const byHead = new Map<string, ToolCall[][]>();
    const seenMessageIds = new Set<string>();
    let current: { headId: string; batches: ToolCall[][] } | null = null;

    for (const { message, toolCalls } of processedMessages) {
      const messageId = message.id;
      if (!messageId || message.type === "human") {
        current = null;
        continue;
      }
      seenMessageIds.add(messageId);

      let regular: ToolCall[];
      const cached = regularCache.get(messageId);
      if (cached && cached.source === toolCalls) {
        regular = cached.regular;
      } else {
        regular = toolCalls.filter((tc) => tc.name !== "task");
        if (
          cached &&
          cached.regular.length === regular.length &&
          cached.regular.every((tc, i) => tc === regular[i])
        ) {
          regular = cached.regular;
        }
        regularCache.set(messageId, { source: toolCalls, regular });
      }

      if (extractStringFromMessageContent(message).trim() !== "") current = null;
      if (regular.length > 0) {
        if (!current) current = { headId: messageId, batches: [] };
        current.batches.push(regular);
        byHead.set(current.headId, current.batches);
      }
      if (toolCalls.some((tc) => tc.name === "task" && !!tc.args["subagent_type"])) {
        current = null;
      }
    }

    for (const [headId, batches] of byHead) {
      const prev = batchesCache.get(headId);
      if (prev && prev.length === batches.length && prev.every((b, i) => b === batches[i])) {
        byHead.set(headId, prev);
      } else {
        batchesCache.set(headId, batches);
      }
    }
    for (const id of batchesCache.keys()) if (!byHead.has(id)) batchesCache.delete(id);
    for (const id of regularCache.keys()) if (!seenMessageIds.has(id)) regularCache.delete(id);

    // A transcript that ends inside a run may still grow another batch — its
    // head holds the group open across think-pauses while the run is live.
    return { toolBatchesByHead: byHead, openEndedHeadId: current?.headId ?? null };
  }, [processedMessages]);

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
      {/* Shared picker for the hero upload CTA and the composer paperclip. */}
      <input
        ref={attachInputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        onChange={onInputChange}
      />
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
              {showUploadCta && (
                <button
                  type="button"
                  onClick={() => attachInputRef.current?.click()}
                  disabled={uploading}
                  {...dropHandlers}
                  className={cn(
                    "mt-8 flex w-full max-w-[480px] items-center gap-3.5 rounded-2xl border border-dashed border-border2 bg-surface-raised/70 px-5 py-4 text-left shadow-sm transition-colors hover:border-brand-strong hover:bg-brand-accent-soft/40 disabled:opacity-60",
                    dragActive && "border-brand-strong bg-brand-accent-soft/40"
                  )}
                >
                  <span className="grid size-10 shrink-0 place-items-center rounded-[9px] bg-brand-accent-soft text-brand-accent">
                    {uploading ? (
                      <Loader2 size={18} className="animate-spin" />
                    ) : (
                      <Upload size={18} />
                    )}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[14px] font-semibold text-primary">
                      Add your resume or a job description
                    </span>
                    <span className="mt-0.5 block text-[12.5px] text-tertiary">
                      {uploading
                        ? "Uploading…"
                        : "Click to browse or drop files — PDF, DOC, DOCX, TXT, MD"}
                    </span>
                  </span>
                </button>
              )}
              <div
                className={cn(
                  "flex max-w-[560px] flex-wrap justify-center gap-2.5",
                  showUploadCta ? "mt-5" : "mt-7"
                )}
              >
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
              {processedMessages.map((data) => {
                const messageId = data.message.id;
                const toolBatches = messageId ? toolBatchesByHead.get(messageId) : undefined;
                return (
                  <ChatMessage
                    key={messageId}
                    message={data.message}
                    toolCalls={data.toolCalls}
                    toolBatches={toolBatches ?? null}
                    isOpenEndedGroup={messageId != null && messageId === openEndedHeadId}
                    isLoading={isLoading}
                    showAvatar={data.showAvatar}
                    actionRequestsMap={actionRequestsMap}
                    reviewConfigsMap={reviewConfigsMap}
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
                  {COMPOSER_ATTACH_ENABLED && (
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
                  )}
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
