"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStream } from "@langchain/react";
import { type Assistant } from "@langchain/langgraph-sdk";
import { BaseMessage, HumanMessage } from "@langchain/core/messages";
import type { TodoItem, ToolApprovalInterruptData } from "@/app/types/types";
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
  messages: BaseMessage[];
  todos: TodoItem[];
  files: Record<string, string>;
  email?: {
    id?: string;
    subject?: string;
    page_content?: string;
  };
};

export function useChat({
  activeAssistant,
  onHistoryRevalidate,
}: {
  activeAssistant: Assistant | null;
  onHistoryRevalidate?: () => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const client = useClient();

  // The v2 stream runtime handles reattach-to-in-flight-runs and thread
  // hydration automatically (no reconnectOnMount / fetchStateHistory), and
  // its root projections are root-namespace-only, so subagent output never
  // bleeds into `messages` (no filterSubagentMessages). Subagent progress
  // is exposed via `stream.subagents` + the scoped selector hooks.
  const stream = useStream<StateType, ToolApprovalInterruptData>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    // Until the assistant resolves, assistantId is "" and hydrating a thread
    // would throw (ThreadStream requires an assistantId). Hold the thread
    // back; the assistantId change recreates the controller, which then
    // hydrates this thread on activate().
    threadId: activeAssistant ? (threadId ?? null) : null,
    onThreadId: setThreadId,
    // Thread-list freshness: run accepted + run ended (any terminal reason,
    // including errors and interrupts).
    onCreated: () => onHistoryRevalidate?.(),
    onCompleted: () => onHistoryRevalidate?.(),
  });

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
      // Optimistic echo is built in: the message renders immediately with a
      // client-minted id that the server echo reconciles against.
      void stream.submit(
        { messages: [new HumanMessage(content)] },
        { config: buildSubmitConfig({ recursion_limit: 100 }) }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, buildSubmitConfig, onHistoryRevalidate]
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

  const resumeInterrupt = useCallback(
    (value: unknown) => {
      // Resumes the newest unresolved interrupt; our HITL flow raises a
      // single interrupt carrying all action_requests, so no interruptId.
      void stream.respond(value, { config: buildSubmitConfig() });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, buildSubmitConfig, onHistoryRevalidate]
  );

  const stopStream = useCallback(() => {
    void stream.stop();
  }, [stream]);

  return {
    stream,
    todos: stream.values.todos ?? [],
    files: filesMap,
    email: stream.values.email,
    setFiles,
    refreshFiles,
    removeFile,
    removeFiles,
    input,
    setInput,
    appendUploadNote,
    focusComposerNonce,
    messages: stream.messages,
    isLoading: stream.isLoading,
    isThreadLoading: stream.isThreadLoading,
    interrupt: stream.interrupt,
    sendMessage,
    stopStream,
    resumeInterrupt,
    // Subagent discovery map, keyed by the `task` tool-call id that spawned
    // each subagent. Content (nested tool calls) is fetched lazily via the
    // scoped selector hooks (`useToolCalls(stream, snapshot)`).
    subagents: stream.subagents,
  };
}
