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
import { useThreadsPanel } from "@/app/hooks/useThreadsPanel";
import { ChatProvider } from "@/providers/ChatProvider";
import { FilePreviewProvider } from "@/providers/FilePreviewProvider";
import { ChatInterface } from "@/app/components/ChatInterface";
import { Workspace } from "@/app/components/Workspace";
import { cn } from "@/lib/utils";

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

  const [mutateThreads, setMutateThreads] = useState<(() => void) | null>(null);
  const [interruptCount, setInterruptCount] = useState(0);
  const [assistant, setAssistant] = useState<Assistant | null>(null);

  const {
    open: threadsOpen,
    pinned: threadsPinned,
    toggle: toggleThreads,
    close: closeThreads,
    togglePin: toggleThreadsPin,
    onThreadSelected,
  } = useThreadsPanel();

  const handleThreadSelect = useCallback(
    (id: string) => {
      setThreadId(id);
      onThreadSelected();
    },
    [setThreadId, onThreadSelected]
  );

  // The threads panel sits outside the panel group, so the main layout is a
  // fixed two panels (chat + workspace). Bumped id so stale 3-panel saved
  // layouts (with a thread-history panel) don't conflict with the new panel
  // set.
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
        <FilePreviewProvider>
          <div className="flex h-screen flex-col bg-background text-foreground">
            <TopBar
              assistant={assistant}
              threadId={threadId}
              interruptCount={interruptCount}
              threadsOpen={threadsOpen}
              onToggleThreads={toggleThreads}
              onOpenSettings={() => setConfigDialogOpen(true)}
              onNewThread={() => setThreadId(null)}
            />

            <div className="relative flex flex-1 overflow-hidden bg-canvas">
              {/* Threads: one always-docked, collapsible panel. The wrapper
                  animates width 0↔var(--sidebar-width); the inner aside keeps
                  a fixed width so content never reflows mid-slide, and is
                  right-anchored (justify-end) so the panel and its border-r
                  hairline slide with the moving edge. */}
              <div
                id="threads-panel"
                inert={!threadsOpen}
                className={cn(
                  "flex shrink-0 justify-end overflow-hidden transition-[width] duration-200 ease-in-out motion-reduce:transition-none",
                  "max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-40",
                  threadsOpen ? "w-[var(--sidebar-width)]" : "w-0"
                )}
              >
                <aside
                  aria-label="Threads"
                  className="flex w-[var(--sidebar-width)] shrink-0 flex-col border-r border-border bg-surface max-lg:shadow-[var(--shadow-lg)]"
                >
                  <ThreadList
                    pinned={threadsPinned}
                    onTogglePin={toggleThreadsPin}
                    onThreadSelect={handleThreadSelect}
                    onMutateReady={(fn) => setMutateThreads(() => fn)}
                    onClose={closeThreads}
                    onInterruptCountChange={setInterruptCount}
                  />
                </aside>
              </div>

              {/* Below lg the panel overlays the content row; the scrim gives
                  click-out and fades on the same 200ms. The top bar (and its
                  toggle) stays clickable. */}
              <div
                onClick={closeThreads}
                aria-hidden="true"
                className={cn(
                  "absolute inset-0 z-30 bg-[var(--scrim)] transition-opacity duration-200 motion-reduce:transition-none lg:hidden",
                  threadsOpen ? "opacity-100" : "pointer-events-none opacity-0"
                )}
              />

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
          </div>
        </FilePreviewProvider>
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
