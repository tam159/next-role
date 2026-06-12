"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useToolCalls, type AnyStream, type SubagentDiscoverySnapshot } from "@langchain/react";
import { useChatContext } from "@/providers/ChatProvider";
import { extractSources, extractSourcesFromToolCalls } from "@/app/utils/sources";
import type { Source } from "@/app/types/types";
import { PlanSection } from "@/app/components/workspace/PlanSection";
import { FilesSection } from "@/app/components/workspace/FilesSection";
import { SourcesSection } from "@/app/components/workspace/SourcesSection";

/**
 * Invisible per-subagent subscription: search tool calls executed by
 * subagents (e.g. hiring-recon's web_search) never reach the root
 * `messages`, so each discovered subagent gets a probe that watches its
 * scoped tool-call projection and reports extracted sources upward. The
 * subscription is ref-counted and shared with the chat's SubagentCard.
 */
function SubagentSourcesProbe({
  stream,
  snapshot,
  onSources,
}: {
  stream: AnyStream;
  snapshot: SubagentDiscoverySnapshot;
  onSources: (id: string, sources: Source[]) => void;
}) {
  const toolCalls = useToolCalls(stream, snapshot);
  const sources = useMemo(() => extractSourcesFromToolCalls(toolCalls), [toolCalls]);
  useEffect(() => {
    onSources(snapshot.id, sources);
  }, [snapshot.id, sources, onSources]);
  return null;
}

export function Workspace() {
  const {
    stream,
    todos,
    files,
    setFiles,
    removeFile,
    removeFiles,
    messages,
    isLoading,
    interrupt,
    subagents,
  } = useChatContext();

  const [subagentSources, setSubagentSources] = useState<Map<string, Source[]>>(new Map());

  const handleSubagentSources = useCallback((id: string, sources: Source[]) => {
    setSubagentSources((prev) => {
      const existing = prev.get(id);
      const same =
        existing &&
        existing.length === sources.length &&
        existing.every((s, i) => s.url === sources[i].url);
      if (same || (!existing && sources.length === 0)) return prev;
      const next = new Map(prev);
      next.set(id, sources);
      return next;
    });
  }, []);

  // Drop entries for subagents that no longer exist (e.g. thread switch).
  useEffect(() => {
    setSubagentSources((prev) => {
      if (![...prev.keys()].some((id) => !subagents.has(id))) return prev;
      const next = new Map<string, Source[]>();
      for (const [id, s] of prev) {
        if (subagents.has(id)) next.set(id, s);
      }
      return next;
    });
  }, [subagents]);

  const sources = useMemo(() => {
    const merged = extractSources(messages);
    const seen = new Set(merged.map((s) => s.url));
    for (const list of subagentSources.values()) {
      for (const s of list) {
        if (seen.has(s.url)) continue;
        seen.add(s.url);
        merged.push(s);
      }
    }
    return merged;
  }, [messages, subagentSources]);

  const [planOpen, setPlanOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(true);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const prevTodos = useRef(todos.length);
  const prevFiles = useRef(Object.keys(files).length);
  const prevSources = useRef(sources.length);

  useEffect(() => {
    if (prevTodos.current === 0 && todos.length > 0) setPlanOpen(true);
    prevTodos.current = todos.length;
  }, [todos.length]);

  const filesCount = Object.keys(files).length;
  useEffect(() => {
    if (prevFiles.current === 0 && filesCount > 0) setFilesOpen(true);
    prevFiles.current = filesCount;
  }, [filesCount]);

  useEffect(() => {
    if (prevSources.current === 0 && sources.length > 0) setSourcesOpen(true);
    prevSources.current = sources.length;
  }, [sources.length]);

  const editDisabled = isLoading === true || interrupt !== undefined;
  const showPlan = todos.length > 0;
  const showSources = sources.length > 0;

  return (
    <div className="flex h-full flex-col border-l border-border bg-background">
      {Array.from(subagents.values()).map((snapshot) => (
        <SubagentSourcesProbe
          key={snapshot.id}
          stream={stream}
          snapshot={snapshot}
          onSources={handleSubagentSources}
        />
      ))}
      <div className="shrink-0 border-b border-border bg-surface/80 px-6 py-4 backdrop-blur-sm">
        <h2 className="text-xl font-bold tracking-tight text-foreground">Workspace</h2>
        <p className="text-sm text-muted-foreground">Plan, files, and sources</p>
      </div>
      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain">
        <div className="flex flex-col gap-3 p-4">
          {showPlan && (
            <PlanSection todos={todos} open={planOpen} onToggle={() => setPlanOpen((v) => !v)} />
          )}
          <FilesSection
            files={files}
            setFiles={setFiles}
            removeFile={removeFile}
            removeFiles={removeFiles}
            editDisabled={editDisabled}
            open={filesOpen}
            onToggle={() => setFilesOpen((v) => !v)}
          />
          {showSources && (
            <SourcesSection
              sources={sources}
              open={sourcesOpen}
              onToggle={() => setSourcesOpen((v) => !v)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
