"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { isHumanMessage } from "@langchain/core/messages";
import { Check, ChevronDown, MessageSquare, Moon, Plus, Settings, Sun } from "lucide-react";
import { Assistant } from "@langchain/langgraph-sdk";
import { LogoMark } from "@/app/components/LogoMark";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/providers/ChatProvider";
import { extractStringFromMessageContent } from "@/app/utils/utils";
import { cn } from "@/lib/utils";

interface TopBarProps {
  assistant: Assistant | null;
  threadId: string | null;
  interruptCount: number;
  onOpenThreads: () => void;
  onOpenSettings: () => void;
  onNewThread: () => void;
}

// Read-only roster: the Career Agent and the specialist subagents it delegates
// to (via the `task` tool). Not a switcher — surfaces the multi-agent system.
const AGENT_ROSTER = [
  {
    name: "Career Agent",
    desc: "Orchestrates your end-to-end prep",
    color: "var(--brand-accent)",
    lead: true,
  },
  { name: "Resume Tailor", desc: "Rewrites your resume against the JD", color: "#0e9f6e" },
  { name: "Interview Coach", desc: "STAR stories, round-by-round", color: "#2563eb" },
  { name: "Company Research", desc: "Live recon on the company & role", color: "#d9785a" },
];

const ICON_BTN =
  "grid size-[38px] place-items-center rounded-[10px] border border-transparent text-secondary transition-colors hover:bg-surface3 hover:text-primary";

export function TopBar({
  assistant,
  threadId,
  interruptCount,
  onOpenThreads,
  onOpenSettings,
  onNewThread,
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

  const threadTitle = useMemo(() => {
    if (!threadId) return "New thread";
    const firstHuman = messages.find(isHumanMessage);
    if (!firstHuman) return "Conversation";
    const txt = extractStringFromMessageContent(firstHuman).trim().replace(/\s+/g, " ");
    if (!txt) return "Conversation";
    return txt.length > 42 ? `${txt.slice(0, 42)}…` : txt;
  }, [threadId, messages]);
  const threadSub = threadId ? "Interview prep" : "Start a new prep";

  const assistantName =
    assistant?.name && assistant.name !== assistant.graph_id ? assistant.name : "Career Agent";

  return (
    <header className="relative z-30 flex h-[60px] shrink-0 items-center justify-between border-b border-primary bg-surface px-4">
      {/* LEFT */}
      <div className="flex min-w-0 items-center gap-2.5">
        <button onClick={onOpenThreads} title="Threads" className={cn(ICON_BTN, "relative")}>
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
        {/* Read-only assistant roster */}
        <div className="relative" ref={rosterRef}>
          <button
            onClick={() => setRosterOpen((o) => !o)}
            className="flex h-[38px] items-center gap-2 rounded-[10px] border border-primary bg-surface-raised px-3 text-[13.5px] font-medium text-primary transition-colors hover:bg-surface3"
          >
            <span
              className="size-[7px] rounded-full"
              style={{
                background: "var(--brand-accent)",
                boxShadow: "0 0 0 3px var(--brand-accent-soft)",
              }}
            />
            <span className="hidden md:inline">{assistantName}</span>
            <ChevronDown className="size-3.5 text-tertiary" />
          </button>
          {rosterOpen && (
            <div className="absolute top-[46px] right-0 z-50 w-[272px] rounded-[14px] border border-primary bg-surface-raised p-1.5 shadow-[var(--shadow-lg)]">
              <div className="px-2.5 pt-2 pb-1.5 text-[11px] font-bold tracking-[0.07em] text-tertiary uppercase">
                Your prep team
              </div>
              {AGENT_ROSTER.map((a) => (
                <div key={a.name} className="flex items-start gap-2.5 rounded-[10px] px-2.5 py-2">
                  <span
                    className="mt-[5px] size-2 shrink-0 rounded-full"
                    style={{ background: a.color }}
                  />
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[13.5px] font-semibold text-primary">{a.name}</span>
                    <span className="text-xs leading-snug text-secondary">{a.desc}</span>
                  </span>
                  {a.lead && (
                    <Check
                      className="mt-0.5 ml-auto size-4 shrink-0 text-brand-accent"
                      strokeWidth={2.4}
                    />
                  )}
                </div>
              ))}
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
