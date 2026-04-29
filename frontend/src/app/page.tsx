"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useQueryState } from "nuqs";
import { getConfig, saveConfig, StandaloneConfig } from "@/lib/config";
import { ConfigDialog } from "@/app/components/ConfigDialog";
import { Button } from "@/components/ui/button";
import { Assistant } from "@langchain/langgraph-sdk";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { Settings, MessagesSquare, SquarePen } from "lucide-react";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { ThreadList } from "@/app/components/ThreadList";
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
      <div className="flex h-screen flex-col bg-background text-foreground">
        <header className="bg-surface/90 flex h-16 items-center justify-between border-b border-border px-6 shadow-sm backdrop-blur">
          <div className="flex items-center gap-4">
            <div>
              <h1 className="text-lg font-semibold tracking-tight">
                GenAI-Accelerated Career Advancement
              </h1>
              <p className="text-xs text-muted-foreground">
                Take the friction out of landing your next big opportunity
              </p>
            </div>
            {!sidebar && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSidebar("1")}
                className="hover:border-primary/25 rounded-full border border-border bg-card px-3 py-2 text-foreground shadow-xs hover:bg-accent"
              >
                <MessagesSquare className="mr-2 h-4 w-4" />
                Threads
                {interruptCount > 0 && (
                  <span className="ml-2 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">
                    {interruptCount}
                  </span>
                )}
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden rounded-full border border-border bg-background/70 px-3 py-1.5 text-xs text-muted-foreground md:block">
              <span className="font-medium">Assistant:</span> {config.assistantId}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfigDialogOpen(true)}
              className="rounded-full bg-card"
            >
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setThreadId(null)}
              disabled={!threadId}
              className="text-primary-foreground hover:bg-primary/90 rounded-full border-primary bg-primary shadow-sm"
            >
              <SquarePen className="mr-2 h-4 w-4" />
              New Thread
            </Button>
          </div>
        </header>

        <div className="flex-1 overflow-hidden bg-canvas">
          <ChatProvider activeAssistant={assistant} onHistoryRevalidate={() => mutateThreads?.()}>
            <ResizablePanelGroup direction="horizontal" autoSaveId="standalone-chat">
              {sidebar && (
                <>
                  <ResizablePanel
                    id="thread-history"
                    order={1}
                    defaultSize={20}
                    minSize={15}
                    className="relative min-w-[320px]"
                  >
                    <ThreadList
                      onThreadSelect={async (id) => {
                        await setThreadId(id);
                      }}
                      onMutateReady={(fn) => setMutateThreads(() => fn)}
                      onClose={() => setSidebar(null)}
                      onInterruptCountChange={setInterruptCount}
                    />
                  </ResizablePanel>
                  <ResizableHandle />
                </>
              )}

              <ResizablePanel
                id="chat"
                className="relative flex flex-col"
                order={2}
                defaultSize={50}
                minSize={30}
              >
                <ChatInterface assistant={assistant} />
              </ResizablePanel>
              <ResizableHandle />
              <ResizablePanel
                id="workspace"
                order={3}
                defaultSize={50}
                minSize={25}
                className="relative flex flex-col"
              >
                <Workspace />
              </ResizablePanel>
            </ResizablePanelGroup>
          </ChatProvider>
        </div>
      </div>
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
