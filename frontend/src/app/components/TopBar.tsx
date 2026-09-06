"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { isHumanMessage } from "@langchain/core/messages";
import { Check, ChevronDown, MessageSquare, Moon, Plus, Settings, Sun } from "lucide-react";
import { AGENT_IDS, CAREER_AGENT_ID, CAREER_SUBAGENTS, agentMeta } from "@/app/config/agents";
import { LogoMark } from "@/app/components/LogoMark";
import { UserMenu } from "@/app/components/auth/UserMenu";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { cn } from "@/lib/utils";

interface TopBarProps {
  threadId: string | null;
  interruptCount: number;
  threadsOpen: boolean;
  onToggleThreads: () => void;
  onOpenSettings: () => void;
  onNewThread: () => void;
  /** Graph id of the agent currently selected. */
  activeAgentId: string;
  /** Per-graph verdict from `GET /agents/available`; missing means allowed. */
  agentAvailability: Record<string, boolean>;
  onSelectAgent: (graphId: string) => void;
}

const ICON_BTN =
  "grid size-[38px] place-items-center rounded-[10px] border border-transparent text-secondary transition-colors hover:bg-surface3 hover:text-primary";

export function TopBar({
  threadId,
  interruptCount,
  threadsOpen,
  onToggleThreads,
  onOpenSettings,
  onNewThread,
  activeAgentId,
  agentAvailability,
  onSelectAgent,
}: TopBarProps) {
  const { messages } = useChatContext();
  const { resolvedTheme, setTheme } = useTheme();

  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = mounted && resolvedTheme === "dark";

  const [rosterOpen, setRosterOpen] = useState(false);
  const rosterRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!rosterOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rosterRef.current && !rosterRef.current.contains(e.target as Node)) setRosterOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setRosterOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [rosterOpen]);

  const activeAgent = agentMeta(activeAgentId);
  const isCareerAgent = activeAgentId === CAREER_AGENT_ID;

  const threadTitle = useMemo(() => {
    if (!threadId) return "New thread";
    const firstHuman = messages.find(isHumanMessage);
    if (!firstHuman) return "Conversation";
    const txt = extractStringFromMessageContent(firstHuman).trim().replace(/\s+/g, " ");
    if (!txt) return "Conversation";
    return txt.length > 42 ? `${txt.slice(0, 42)}…` : txt;
  }, [threadId, messages]);
  const threadSub = threadId ? activeAgent.name : `New ${activeAgent.name} conversation`;

  return (
    <header className="relative z-30 flex h-[60px] shrink-0 items-center justify-between border-b border-primary bg-surface px-4">
      {/* LEFT */}
      <div className="flex min-w-0 items-center gap-2.5">
        <button
          onClick={onToggleThreads}
          title="Threads"
          aria-expanded={threadsOpen}
          aria-controls="threads-panel"
          className={cn(ICON_BTN, "relative")}
        >
          <MessageSquare className="size-[19px]" strokeWidth={1.7} />
          {interruptCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 grid min-h-4 min-w-4 place-items-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">
              {interruptCount}
            </span>
          )}
        </button>
        <div className="flex items-center gap-2.5 pl-0.5">
          <LogoMark size={29} />
          <span className="text-[17px] font-bold tracking-[-0.02em] text-primary">NextRole</span>
        </div>
        <div className="mx-1 hidden h-6 w-px shrink-0 bg-border2 sm:block" />
        <div className="hidden min-w-0 flex-col leading-tight sm:flex">
          <span className="truncate text-sm font-semibold text-primary">{threadTitle}</span>
          <span className="truncate text-xs text-tertiary">{threadSub}</span>
        </div>
      </div>

      {/* RIGHT */}
      <div className="flex shrink-0 items-center gap-1.5">
        {/* Agent picker: which graph new conversations run on */}
        <div className="relative" ref={rosterRef}>
          <button
            onClick={() => setRosterOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={rosterOpen}
            className="flex h-[38px] items-center gap-2 rounded-[10px] border border-primary bg-surface-raised px-3 text-[13.5px] font-medium text-primary transition-colors hover:bg-surface3"
          >
            <span
              className="size-[7px] rounded-full"
              style={{
                background: activeAgent.color,
                boxShadow: `0 0 0 3px color-mix(in srgb, ${activeAgent.color} 22%, transparent)`,
              }}
            />
            <span className="hidden md:inline">{activeAgent.name}</span>
            <ChevronDown className="size-3.5 text-tertiary" />
          </button>
          {rosterOpen && (
            <div
              role="menu"
              className="absolute top-[46px] right-0 z-50 w-[288px] rounded-[14px] border border-primary bg-surface-raised p-1.5 shadow-[var(--shadow-lg)]"
            >
              <div className="px-2.5 pt-2 pb-1.5 text-[11px] font-bold tracking-[0.07em] text-tertiary uppercase">
                Agents
              </div>
              {AGENT_IDS.map((graphId) => {
                const agent = agentMeta(graphId);
                const allowed = agentAvailability[graphId] !== false;
                const active = graphId === activeAgentId;
                return (
                  <button
                    key={graphId}
                    role="menuitem"
                    disabled={!allowed}
                    title={allowed ? undefined : "Administrators only"}
                    onClick={() => {
                      setRosterOpen(false);
                      if (!active) onSelectAgent(graphId);
                    }}
                    className={cn(
                      "flex w-full items-start gap-2.5 rounded-[10px] px-2.5 py-2 text-left transition-colors",
                      allowed ? "hover:bg-surface3" : "cursor-not-allowed opacity-50"
                    )}
                  >
                    <span
                      className="mt-[5px] size-2 shrink-0 rounded-full"
                      style={{ background: agent.color }}
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[13.5px] font-semibold text-primary">{agent.name}</span>
                      <span className="text-xs leading-snug text-secondary">
                        {allowed ? agent.description : "Administrators only"}
                      </span>
                    </span>
                    {active && (
                      <Check
                        className="mt-0.5 ml-auto size-4 shrink-0 text-brand-accent"
                        strokeWidth={2.4}
                      />
                    )}
                  </button>
                );
              })}
              {isCareerAgent && (
                <>
                  <div className="mx-2.5 my-1.5 h-px bg-border2" />
                  <div className="px-2.5 pb-1.5 text-[11px] font-bold tracking-[0.07em] text-tertiary uppercase">
                    Its prep team
                  </div>
                  {CAREER_SUBAGENTS.map((sub) => (
                    <div
                      key={sub.name}
                      className="flex items-start gap-2.5 rounded-[10px] px-2.5 py-2"
                    >
                      <span
                        className="mt-[5px] size-2 shrink-0 rounded-full"
                        style={{ background: sub.color }}
                      />
                      <span className="flex min-w-0 flex-col">
                        <span className="text-[13.5px] font-semibold text-primary">{sub.name}</span>
                        <span className="text-xs leading-snug text-secondary">
                          {sub.description}
                        </span>
                      </span>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>

        <button
          onClick={() => setTheme(isDark ? "light" : "dark")}
          title="Toggle theme"
          className={ICON_BTN}
          suppressHydrationWarning
        >
          {isDark ? (
            <Sun className="size-[18px]" strokeWidth={1.7} />
          ) : (
            <Moon className="size-[18px]" strokeWidth={1.7} />
          )}
        </button>

        <button onClick={onOpenSettings} title="Settings" className={ICON_BTN}>
          <Settings className="size-[18px]" strokeWidth={1.7} />
        </button>

        <UserMenu />

        <Button
          variant="primary"
          onClick={onNewThread}
          disabled={!threadId}
          className="ml-1 h-[38px] gap-1.5 rounded-[10px] px-3.5"
        >
          <Plus className="size-4" />
          New thread
        </Button>
      </div>
    </header>
  );
}
