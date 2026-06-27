"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useQueryState } from "nuqs";
import { getConfig, saveConfig, StandaloneConfig } from "@/lib/config";
import { ConfigDialog } from "@/app/components/ConfigDialog";
import { Button } from "@/components/ui/button";
import { Assistant } from "@langchain/langgraph-sdk";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { TopBar } from "@/app/components/TopBar";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useDefaultLayout } from "react-resizable-panels";
import { ThreadList } from "@/app/components/ThreadList";
import { ThreadsDrawer } from "@/app/components/ThreadsDrawer";
import { ChatProvider } from "@/providers/ChatProvider";
import { ChatInterface } from "@/app/components/ChatInterface";
import { Workspace } from "@/app/components/Workspace";

interface HomePageInnerProps {
  config: StandaloneConfig;
  configDialogOpen: boolean;
  setConfigDialogOpen: (open: boolean) => void;
  handleSaveConfig: (config: StandaloneConfig) => void;
}

function HomePageInner({
  config,
  configDialogOpen,
  setConfigDialogOpen,
  handleSaveConfig,
}: HomePageInnerProps) {
  const client = useClient();
  const [threadId, setThreadId] = useQueryState("threadId");
  const [sidebar, setSidebar] = useQueryState("sidebar");

  const [mutateThreads, setMutateThreads] = useState<(() => void) | null>(null);
  const [interruptCount, setInterruptCount] = useState(0);
  const [assistant, setAssistant] = useState<Assistant | null>(null);

  // Threads can be pinned to a persistent docked column (stays open while
  // switching threads) or used as an overlay drawer. Pinned state is a
  // persisted preference.
  const [threadsPinned, setThreadsPinnedState] = useState(false);
  useEffect(() => {
    setThreadsPinnedState(localStorage.getItem("nr-threads-pinned") === "1");
  }, []);
  const setThreadsPinned = useCallback((value: boolean) => {
    setThreadsPinnedState(value);
    try {
      localStorage.setItem("nr-threads-pinned", value ? "1" : "0");
    } catch {
      // ignore storage failures
    }
  }, []);

  const handleThreadSelect = useCallback(
    async (id: string) => {
      await setThreadId(id);
      if (!threadsPinned) await setSidebar(null);
    },
    [setThreadId, setSidebar, threadsPinned]
  );

  // Threads now live in a slide-over drawer, so the main layout is a fixed two
  // panels (chat + workspace). Bumped id so stale 3-panel saved layouts (with a
  // thread-history panel) don't conflict with the new panel set.
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: "standalone-chat-v2",
    panelIds: ["chat", "workspace"],
  });

  const fetchAssistant = useCallback(async () => {
    const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      config.assistantId
    );

    if (isUUID) {
      // We should try to fetch the assistant directly with this UUID
      try {
        const data = await client.assistants.get(config.assistantId);
        setAssistant(data);
      } catch (error) {
        console.error("Failed to fetch assistant:", error);
        setAssistant({
          assistant_id: config.assistantId,
          graph_id: config.assistantId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          config: {},
          metadata: {},
          version: 1,
          name: "Assistant",
          context: {},
        });
      }
    } else {
      try {
        // We should try to list out the assistants for this graph, and then use the default one.
        // TODO: Paginate this search, but 100 should be enough for graph name
        const assistants = await client.assistants.search({
          graphId: config.assistantId,
          limit: 100,
        });
        const defaultAssistant = assistants.find(
          (assistant) => assistant.metadata?.["created_by"] === "system"
        );
        if (defaultAssistant === undefined) {
          throw new Error("No default assistant found");
        }
        setAssistant(defaultAssistant);
      } catch (error) {
        console.error(
          "Failed to find default assistant from graph_id: try setting the assistant_id directly:",
          error
        );
        setAssistant({
          assistant_id: config.assistantId,
          graph_id: config.assistantId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          config: {},
          metadata: {},
          version: 1,
          name: config.assistantId,
          context: {},
        });
      }
    }
  }, [client, config.assistantId]);

  useEffect(() => {
    fetchAssistant();
  }, [fetchAssistant]);

  return (
    <>
      <ConfigDialog
        open={configDialogOpen}
        onOpenChange={setConfigDialogOpen}
        onSave={handleSaveConfig}
        initialConfig={config}
      />
      <ChatProvider activeAssistant={assistant} onHistoryRevalidate={() => mutateThreads?.()}>
        <div className="flex h-screen flex-col bg-background text-foreground">
          <TopBar
            assistant={assistant}
            threadId={threadId}
            interruptCount={interruptCount}
            onOpenThreads={() => (threadsPinned ? setThreadsPinned(false) : setSidebar("1"))}
            onOpenSettings={() => setConfigDialogOpen(true)}
            onNewThread={() => setThreadId(null)}
          />

          <div className="flex flex-1 overflow-hidden bg-canvas">
            {/* Pinned → persistent docked column (stays open while switching). */}
            {threadsPinned && (
              <aside className="flex w-[300px] shrink-0 flex-col border-r border-border bg-surface">
                <ThreadList
                  pinned
                  onTogglePin={() => setThreadsPinned(false)}
                  onThreadSelect={handleThreadSelect}
                  onMutateReady={(fn) => setMutateThreads(() => fn)}
                  onInterruptCountChange={setInterruptCount}
                />
              </aside>
            )}

            <div className="min-w-0 flex-1">
              <ResizablePanelGroup
                orientation="horizontal"
                defaultLayout={defaultLayout}
                onLayoutChanged={onLayoutChanged}
              >
                <ResizablePanel
                  id="chat"
                  className="relative flex flex-col"
                  defaultSize="46%"
                  minSize="30%"
                >
                  <ChatInterface assistant={assistant} />
                </ResizablePanel>
                <ResizableHandle />
                <ResizablePanel
                  id="workspace"
                  defaultSize="54%"
                  minSize="25%"
                  className="relative flex flex-col"
                >
                  <Workspace />
                </ResizablePanel>
              </ResizablePanelGroup>
            </div>
          </div>

          {/* Unpinned → overlay drawer that auto-closes on select. */}
          {!threadsPinned && (
            <ThreadsDrawer open={!!sidebar} onOpenChange={(o) => setSidebar(o ? "1" : null)}>
              <ThreadList
                onTogglePin={() => {
                  setThreadsPinned(true);
                  setSidebar(null);
                }}
                onThreadSelect={handleThreadSelect}
                onMutateReady={(fn) => setMutateThreads(() => fn)}
                onClose={() => setSidebar(null)}
                onInterruptCountChange={setInterruptCount}
              />
            </ThreadsDrawer>
          )}
        </div>
      </ChatProvider>
    </>
  );
}

function HomePageContent() {
  const [config, setConfig] = useState<StandaloneConfig | null>(null);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [assistantId, setAssistantId] = useQueryState("assistantId");

  // On mount, check for saved config, otherwise show config dialog
  useEffect(() => {
    const savedConfig = getConfig();
    if (savedConfig) {
      setConfig(savedConfig);
      if (!assistantId) {
        setAssistantId(savedConfig.assistantId);
      }
    } else {
      setConfigDialogOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If config changes, update the assistantId
  useEffect(() => {
    if (config && !assistantId) {
      setAssistantId(config.assistantId);
    }
  }, [config, assistantId, setAssistantId]);

  const handleSaveConfig = useCallback((newConfig: StandaloneConfig) => {
    saveConfig(newConfig);
    setConfig(newConfig);
  }, []);

  const langsmithApiKey =
    config?.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";

  if (!config) {
    return (
      <>
        <ConfigDialog
          open={configDialogOpen}
          onOpenChange={setConfigDialogOpen}
          onSave={handleSaveConfig}
        />
        <div className="flex h-screen items-center justify-center">
          <div className="text-center">
            <h1 className="text-2xl font-bold">Welcome to Standalone Chat</h1>
            <p className="mt-2 text-muted-foreground">Configure your deployment to get started</p>
            <Button onClick={() => setConfigDialogOpen(true)} className="mt-4">
              Open Configuration
            </Button>
          </div>
        </div>
      </>
    );
  }

  return (
    <ClientProvider deploymentUrl={config.deploymentUrl} apiKey={langsmithApiKey}>
      <HomePageInner
        config={config}
        configDialogOpen={configDialogOpen}
        setConfigDialogOpen={setConfigDialogOpen}
        handleSaveConfig={handleSaveConfig}
      />
    </ClientProvider>
  );
}

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center">
          <p className="text-muted-foreground">Loading...</p>
        </div>
      }
    >
      <HomePageContent />
    </Suspense>
  );
}
