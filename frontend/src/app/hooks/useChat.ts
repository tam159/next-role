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
  writeAgentFile,
  type AgentFile,
} from "@/app/lib/agentFiles";

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

  const sendMessage = useCallback(
    (content: string) => {
      const newMessage: Message = { id: uuidv4(), type: "human", content };
      stream.submit(
        { messages: [newMessage] },
        {
          optimisticValues: (prev) => ({
            messages: [...(prev.messages ?? []), newMessage],
          }),
          config: { ...(activeAssistant?.config ?? {}), recursion_limit: 100 },
          // Surface subgraph events (namespaced) so the SDK's SubagentManager
          // can rebuild per-subagent streams from the `task`-spawned subgraphs.
          streamSubgraphs: true,
        }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
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
          config: activeAssistant?.config,
          checkpoint: checkpoint,
          streamSubgraphs: true,
          ...(isRerunningSubagent ? { interruptAfter: ["tools"] } : { interruptBefore: ["tools"] }),
        });
      } else {
        stream.submit(
          { messages },
          {
            config: activeAssistant?.config,
            interruptBefore: ["tools"],
            streamSubgraphs: true,
          }
        );
      }
    },
    [stream, activeAssistant?.config]
  );

  const graphId = activeAssistant?.graph_id ?? null;
  const assistantId = activeAssistant?.assistant_id ?? null;
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
      const files = await fetchAgentFiles({ client, graphId, assistantId, stateFiles });
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
  }, [client, graphId, assistantId, stateFilesSig]);

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
              assistantId,
              file: { ...existing, content },
            })
          );
        } else if (!existing) {
          // New file: route by virtual path prefix.
          const cfg = getAgentFileSources(graphId);
          let synthesized: AgentFile | null = null;
          if (path.startsWith("/store/") && cfg?.store) {
            synthesized = {
              path,
              content,
              encoding: "utf-8",
              source: "store",
              sourceKey: path.slice("/store".length) || "/",
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
                assistantId,
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
        assistantId,
        stateFiles,
      });
      setExtendedFiles(refreshed);
    },
    [client, threadId, graphId, assistantId, hasExternalSources, stream.values.files]
  );

  const continueStream = useCallback(
    (hasTaskToolCall?: boolean) => {
      stream.submit(undefined, {
        config: {
          ...(activeAssistant?.config || {}),
          recursion_limit: 100,
        },
        streamSubgraphs: true,
        ...(hasTaskToolCall ? { interruptAfter: ["tools"] } : { interruptBefore: ["tools"] }),
      });
      // Update thread list when continuing stream
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
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
        streamSubgraphs: true,
      });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, onHistoryRevalidate]
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
    messages: stream.messages,
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
