"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { extractSources } from "@/app/utils/sources";
import { PlanSection } from "@/app/components/workspace/PlanSection";
import { FilesSection } from "@/app/components/workspace/FilesSection";
import { SourcesSection } from "@/app/components/workspace/SourcesSection";

export function Workspace() {
  const { todos, files, setFiles, removeFile, messages, isLoading, interrupt, subagents } =
    useChatContext();

  // With `filterSubagentMessages: true`, search tool calls executed by
  // subagents (e.g. researcher's web_search) no longer flow into the main
  // `messages` array — they live on each subagent's own `messages`. Merge
  // both sources so the Sources tab keeps working.
  const sources = useMemo(() => {
    const all = [...messages];
    if (subagents) {
      for (const sub of subagents.values()) {
        if (Array.isArray(sub?.messages)) all.push(...sub.messages);
      }
    }
    return extractSources(all);
  }, [messages, subagents]);

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
      <div className="bg-surface/80 flex-shrink-0 border-b border-border px-6 py-4 backdrop-blur">
        <h2 className="text-xl font-bold tracking-tight text-foreground">Workspace</h2>
        <p className="text-sm text-muted-foreground">Plan, files, and sources</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain">
        <div className="flex flex-col gap-3 p-4">
          {showPlan && (
            <PlanSection todos={todos} open={planOpen} onToggle={() => setPlanOpen((v) => !v)} />
          )}
          <FilesSection
            files={files}
            setFiles={setFiles}
            removeFile={removeFile}
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
