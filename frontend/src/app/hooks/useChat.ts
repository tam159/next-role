"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { type Message, type Assistant, type Checkpoint } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import type { UseStream, UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { TodoItem } from "@/app/types/types";
import { useClient } from "@/providers/ClientProvider";
import { useQueryState } from "nuqs";
import {
  fetchAgentFiles,
  getAgentFileSources,
  resolveStoreLocation,
  writeAgentFile,
  type AgentFile,
} from "@/app/lib/agentFiles";
import { deleteAgentFile } from "@/app/lib/uploadFiles";
import { getConfig } from "@/lib/config";

function stateFileSignature(value: unknown): string {
  if (typeof value === "string") return `s:${value.length}:${value.slice(0, 64)}`;
  if (value && typeof value === "object" && "content" in (value as object)) {
    const inner = (value as { content: unknown }).content;
    if (typeof inner === "string") return `o:${inner.length}:${inner.slice(0, 64)}`;
    if (Array.isArray(inner)) return `a:${inner.length}`;
  }
  return `x:${String(value ?? "").length}`;
}

export type StateType = {
  messages: Message[];
  todos: TodoItem[];
  files: Record<string, string>;
  email?: {
    id?: string;
    subject?: string;
    page_content?: string;
  };
  ui?: any;
};

export function useChat({
  activeAssistant,
  onHistoryRevalidate,
  thread,
}: {
  activeAssistant: Assistant | null;
  onHistoryRevalidate?: () => void;
  thread?: UseStreamThread<StateType>;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const client = useClient();

  // Cast to UseStream so the subagent-tracking surface (subagents,
  // activeSubagents, getSubagent*) is visible to TypeScript. The runtime
  // already exposes them, but the default ResolveStreamInterface for a
  // plain StateType resolves to BaseStream (without those getters).
  //
  // `filterSubagentMessages: true` is the switch that actually populates
  // the SubagentManager: without it, the SDK lets subagent messages flow
  // into the main `messages` array and never matches subgraph namespaces
  // to their parent `task` tool call (see ui/manager.js:402, 461). The
  // option is part of AnyStreamOptions and accepted by useStreamLGP at
  // runtime even though it isn't on the public UseStreamOptions type.
  const stream = useStream<StateType>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    reconnectOnMount: true,
    threadId: threadId ?? null,
    onThreadId: setThreadId,
    defaultHeaders: { "x-auth-scheme": "langsmith" },
    // Enable fetching state history when switching to existing threads
    fetchStateHistory: true,
    // Revalidate thread list when stream finishes, errors, or creates new thread
    onFinish: onHistoryRevalidate,
    onError: onHistoryRevalidate,
    onCreated: onHistoryRevalidate,
    experimental_thread: thread,
    filterSubagentMessages: true,
  } as any) as unknown as UseStream<StateType>;

  // Hot-streaming detection: at least one running subagent has a pending
  // tool call (its LLM is streaming tool-call args token-by-token). This is
  // when `stream.messages` and `stream.subagents` churn at the highest rate
  // — multiple subagents emitting in parallel can saturate the main thread
  // and starve user input. While this is true we sample stream state at
  // 80 ms instead of on every token; the trigger is tool-name agnostic so
  // it covers any current or future subagent that streams long tool args.
  let isHotStreaming = false;
  if (stream.isLoading) {
    const subagentsMap = (stream as unknown as { subagents?: unknown }).subagents;
    const iter =
      subagentsMap && typeof (subagentsMap as { values?: unknown }).values === "function"
        ? ((subagentsMap as { values: () => Iterable<any> }).values() as Iterable<any>)
        : null;
    if (iter) {
      for (const s of iter) {
        if (s?.status !== "running" || !Array.isArray(s?.toolCalls)) continue;
        if (s.toolCalls.some((tc: { state?: string }) => tc?.state === "pending")) {
          isHotStreaming = true;
          break;
        }
      }
    }
  }

  // Throttled snapshot of `stream.messages`. Inside the hot window we sample
  // the latest array at 80 ms via `setInterval`; outside it we pass through
  // `stream.messages` directly so non-streaming updates remain instant.
  // Downstream consumers (e.g. ChatInterface's message-processing useMemo,
  // and every derived tool-call array) depend on this snapshot, so their
  // expensive rebuilds run at ~12 fps during the hot window.
  //
  // Subtlety: both `stream.messages` and `stream.subagents` are exposed by
  // the SDK as getters that return a fresh reference on every read. We
  // therefore CANNOT use them as useEffect deps (an unstable dep + setState
  // inside the effect = infinite re-render loop). The effect below depends
  // only on `isHotStreaming` (a derived boolean that flips at most a few
  // times per run); the ref pulls the latest messages at sample time.
  const messagesRef = useRef(stream.messages);
  messagesRef.current = stream.messages;
  const [throttledMessages, setThrottledMessages] = useState(stream.messages);
  useEffect(() => {
    if (!isHotStreaming) return;
    // Sync immediately on entry so consumers don't see a stale snapshot.
    setThrottledMessages(messagesRef.current);
    const id = window.setInterval(() => {
      setThrottledMessages(messagesRef.current);
    }, 80);
    return () => {
      window.clearInterval(id);
      // Final flush so the last tokens land instantly when streaming ends.
      setThrottledMessages(messagesRef.current);
    };
  }, [isHotStreaming]);
  const messagesSnapshot = isHotStreaming ? throttledMessages : stream.messages;

  // Build the `config` arg for every `stream.submit`. Merges:
  //   - assistant-level config (from the LangGraph API)
  //   - per-invocation user model overrides from Settings (configurable.*)
  //   - any caller-supplied overrides (e.g. recursion_limit)
  // The middleware in backend/app/career_agent/middleware.py reads
  // `configurable.main_agent_model` / `configurable.subagent_model`.
  const buildSubmitConfig = useCallback(
    (extra?: Record<string, unknown>) => {
      const assistantConfig = (activeAssistant?.config ?? {}) as Record<string, unknown>;
      const assistantConfigurable = (assistantConfig.configurable ?? {}) as Record<string, unknown>;
      const userConfig = getConfig();
      const modelOverrides: Record<string, string> = {};
      if (userConfig?.mainAgentModel) modelOverrides.main_agent_model = userConfig.mainAgentModel;
      if (userConfig?.subagentModel) modelOverrides.subagent_model = userConfig.subagentModel;

      return {
        ...assistantConfig,
        ...(extra ?? {}),
        configurable: {
          ...assistantConfigurable,
          ...modelOverrides,
        },
      };
    },
    [activeAssistant?.config]
  );

  const sendMessage = useCallback(
    (content: string) => {
      const newMessage: Message = { id: uuidv4(), type: "human", content };
      stream.submit(
        { messages: [newMessage] },
        {
          optimisticValues: (prev) => ({
            messages: [...(prev.messages ?? []), newMessage],
          }),
          config: buildSubmitConfig({ recursion_limit: 100 }),
          // Surface subgraph events (namespaced) so the SDK's SubagentManager
          // can rebuild per-subagent streams from the `task`-spawned subgraphs.
          streamSubgraphs: true,
        }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, buildSubmitConfig, onHistoryRevalidate]
  );

  const runSingleStep = useCallback(
    (
      messages: Message[],
      checkpoint?: Checkpoint,
      isRerunningSubagent?: boolean,
      optimisticMessages?: Message[]
    ) => {
      if (checkpoint) {
        stream.submit(undefined, {
          ...(optimisticMessages ? { optimisticValues: { messages: optimisticMessages } } : {}),
          config: buildSubmitConfig(),
          checkpoint: checkpoint,
          streamSubgraphs: true,
          ...(isRerunningSubagent ? { interruptAfter: ["tools"] } : { interruptBefore: ["tools"] }),
        });
      } else {
        stream.submit(
          { messages },
          {
            config: buildSubmitConfig(),
            interruptBefore: ["tools"],
            streamSubgraphs: true,
          }
        );
      }
    },
    [stream, buildSubmitConfig]
  );

  const graphId = activeAssistant?.graph_id ?? null;
  const hasExternalSources = useMemo(() => Boolean(getAgentFileSources(graphId)), [graphId]);

  // Files surfaced to the UI: state files + (per-agent) store + disk files.
  const [extendedFiles, setExtendedFiles] = useState<AgentFile[]>([]);
  const extendedFilesRef = useRef<AgentFile[]>([]);
  extendedFilesRef.current = extendedFiles;

  // State-source files have no per-key timestamp on the backend. We stamp
  // them client-side on first appearance and on content change so they sort
  // alongside store/disk files. The map persists for the lifetime of the
  // hook (one session); on hard reload, all current state files get the
  // same "now" stamp at mount, then later edits float to the top.
  const stateStampsRef = useRef(new Map<string, { sig: string; at: number }>());

  const stateFilesRaw = stream.values.files ?? {};
  // Stable signature so the effect doesn't refire on every stream tick.
  const stateFilesSig = useMemo(() => JSON.stringify(stateFilesRaw), [stateFilesRaw]);

  const refreshFiles = useCallback(async () => {
    const stateFiles = JSON.parse(stateFilesSig) as Record<string, unknown>;

    const stamps = stateStampsRef.current;
    const seen = new Set<string>();
    const now = Date.now();
    let i = 0;
    for (const [path, value] of Object.entries(stateFiles)) {
      seen.add(path);
      const sig = stateFileSignature(value);
      const prev = stamps.get(path);
      if (!prev || prev.sig !== sig) {
        stamps.set(path, { sig, at: now + i });
      }
      i += 1;
    }
    for (const path of stamps.keys()) {
      if (!seen.has(path)) stamps.delete(path);
    }

    try {
      const files = await fetchAgentFiles({ client, graphId, stateFiles });
      const stamped = files.map((f) => {
        if (f.source !== "state") return f;
        const stamp = stamps.get(f.path)?.at;
        return stamp != null ? { ...f, modifiedAt: stamp } : f;
      });
      stamped.sort((a, b) => {
        const am = a.modifiedAt ?? -Infinity;
        const bm = b.modifiedAt ?? -Infinity;
        if (am !== bm) return bm - am;
        return a.path.localeCompare(b.path);
      });
      setExtendedFiles(stamped);
    } catch (e) {
      console.warn("fetchAgentFiles failed", e);
    }
  }, [client, graphId, stateFilesSig]);

  useEffect(() => {
    refreshFiles().catch(() => {});
    // Refetch on stream completion (isLoading transitions to false) so
    // store/disk writes from the latest run show up.
  }, [refreshFiles, stream.isLoading]);

  const filesMap = useMemo(() => {
    // extendedFiles is already DESC-sorted by modifiedAt; preserving insertion
    // order in a string-keyed object keeps that order for downstream consumers
    // (file paths start with "/" so they're not numeric-string keys).
    const out: Record<string, string> = {};
    for (const f of extendedFiles) out[f.path] = f.content;
    return out;
  }, [extendedFiles]);

  const setFiles = useCallback(
    async (files: Record<string, string>) => {
      // Diff against current to find which paths changed; route each by source.
      const current = extendedFilesRef.current;
      const currentMap = new Map(current.map((f) => [f.path, f]));

      // No external sources configured: preserve original full-state-update behavior.
      if (!hasExternalSources) {
        if (!threadId) return;
        await client.threads.updateState(threadId, { values: { files } });
        return;
      }

      const writes: Promise<void>[] = [];
      const stateFilesNext: Record<string, string> = {};
      let stateChanged = false;
      for (const [path, content] of Object.entries(files)) {
        const existing = currentMap.get(path);
        if (existing && existing.source === "state") {
          stateFilesNext[path] = content;
          if (existing.content !== content) stateChanged = true;
        } else if (existing && existing.content !== content) {
          writes.push(
            writeAgentFile({
              client,
              threadId,
              graphId,
              file: { ...existing, content },
            })
          );
        } else if (!existing) {
          // New file: route by virtual path prefix.
          const cfg = getAgentFileSources(graphId);
          let synthesized: AgentFile | null = null;
          if (cfg?.store && resolveStoreLocation(cfg.store, path)) {
            synthesized = {
              path,
              content,
              encoding: "utf-8",
              source: "store",
              sourceKey: path,
            };
          } else if (cfg?.disk) {
            const top = path.split("/")[1];
            if (top && cfg.disk.includeDirs.includes(top)) {
              synthesized = {
                path,
                content,
                encoding: "utf-8",
                source: "disk",
                sourceKey: `/${cfg.disk.root}${path}`,
              };
            }
          }
          if (synthesized) {
            writes.push(
              writeAgentFile({
                client,
                threadId,
                graphId,
                file: synthesized,
              })
            );
          } else {
            // Fallback: treat as state file.
            stateFilesNext[path] = content;
            stateChanged = true;
          }
        } else {
          // existing && unchanged: keep state files in the next map for state writes.
          if (existing.source === "state") stateFilesNext[path] = content;
        }
      }
      if (stateChanged && threadId) {
        writes.push(
          client.threads
            .updateState(threadId, { values: { files: stateFilesNext } })
            .then(() => undefined)
        );
      }
      await Promise.all(writes);
      // Refetch to pick up new modified_at, etc.
      const stateFiles = (stream.values.files ?? {}) as Record<string, unknown>;
      const refreshed = await fetchAgentFiles({
        client,
        graphId,
        stateFiles,
      });
      setExtendedFiles(refreshed);
    },
    [client, threadId, graphId, hasExternalSources, stream.values.files]
  );

  const removeFile = useCallback(
    async (virtualPath: string) => {
      const file = extendedFilesRef.current.find((f) => f.path === virtualPath);
      if (!file) throw new Error(`File not found: ${virtualPath}`);
      if (file.source !== "disk") {
        throw new Error("Only disk-backed files can be deleted from the UI");
      }
      await deleteAgentFile(file.sourceKey);
      await refreshFiles();
    },
    [refreshFiles]
  );

  const removeFiles = useCallback(
    async (
      virtualPaths: string[]
    ): Promise<{ deleted: string[]; errors: { path: string; reason: string }[] }> => {
      const results = await Promise.allSettled(
        virtualPaths.map(async (vp) => {
          const file = extendedFilesRef.current.find((f) => f.path === vp);
          if (!file) throw new Error(`File not found: ${vp}`);
          if (file.source !== "disk") {
            throw new Error("Only disk-backed files can be deleted from the UI");
          }
          await deleteAgentFile(file.sourceKey);
          return vp;
        })
      );
      const deleted: string[] = [];
      const errors: { path: string; reason: string }[] = [];
      results.forEach((r, i) => {
        if (r.status === "fulfilled") {
          deleted.push(r.value);
        } else {
          const reason =
            r.reason instanceof Error ? r.reason.message : String(r.reason ?? "Delete failed");
          errors.push({ path: virtualPaths[i], reason });
        }
      });
      await refreshFiles();
      return { deleted, errors };
    },
    [refreshFiles]
  );

  // Composer input state, lifted out of ChatInterface so any surface (paperclip,
  // Workspace > Files Upload, future drag-drop) can append a note in one place.
  const [input, setInput] = useState("");
  const [focusComposerNonce, setFocusComposerNonce] = useState(0);

  const appendUploadNote = useCallback((filenames: string[]) => {
    if (filenames.length === 0) return;
    const note = `Uploaded: ${filenames.join(", ")}\n`;
    setInput((prev) => (prev.trim() ? `${prev.replace(/\n+$/, "")}\n\n${note}` : note));
    setFocusComposerNonce((n) => n + 1);
  }, []);

  const continueStream = useCallback(
    (hasTaskToolCall?: boolean) => {
      stream.submit(undefined, {
        config: buildSubmitConfig({ recursion_limit: 100 }),
        streamSubgraphs: true,
        ...(hasTaskToolCall ? { interruptAfter: ["tools"] } : { interruptBefore: ["tools"] }),
      });
      // Update thread list when continuing stream
      onHistoryRevalidate?.();
    },
    [stream, buildSubmitConfig, onHistoryRevalidate]
  );

  const markCurrentThreadAsResolved = useCallback(() => {
    stream.submit(null, {
      command: { goto: "__end__", update: null },
      streamSubgraphs: true,
    });
    // Update thread list when marking thread as resolved
    onHistoryRevalidate?.();
  }, [stream, onHistoryRevalidate]);

  const resumeInterrupt = useCallback(
    (value: any) => {
      stream.submit(null, {
        command: { resume: value },
        config: buildSubmitConfig(),
        streamSubgraphs: true,
      });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, buildSubmitConfig, onHistoryRevalidate]
  );

  const stopStream = useCallback(() => {
    stream.stop();
  }, [stream]);

  return {
    stream,
    todos: stream.values.todos ?? [],
    files: filesMap,
    email: stream.values.email,
    ui: stream.values.ui,
    setFiles,
    refreshFiles,
    removeFile,
    removeFiles,
    input,
    setInput,
    appendUploadNote,
    focusComposerNonce,
    messages: messagesSnapshot,
    isHotStreaming,
    isLoading: stream.isLoading,
    isThreadLoading: stream.isThreadLoading,
    interrupt: stream.interrupt,
    getMessagesMetadata: stream.getMessagesMetadata,
    sendMessage,
    runSingleStep,
    continueStream,
    stopStream,
    markCurrentThreadAsResolved,
    resumeInterrupt,
    // Subagent progress surface from the SDK. Reading these getters is what
    // adds "updates" + "messages-tuple" to stream_mode (both backend-supported).
    subagents: stream.subagents,
    activeSubagents: stream.activeSubagents,
    getSubagent: stream.getSubagent,
    getSubagentsByMessage: stream.getSubagentsByMessage,
    getSubagentsByType: stream.getSubagentsByType,
  };
}
