"use client";

import React, { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface WorkspaceCardProps {
  icon: ReactNode;
  title: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  headerAction?: ReactNode;
}

export function WorkspaceCard({
  icon,
  title,
  count,
  open,
  onToggle,
  children,
  headerAction,
}: WorkspaceCardProps) {
  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-sm transition-colors hover:border-brand-strong/30">
      <div className="flex items-center gap-3 px-4 py-3.5 transition-colors hover:bg-surface3/60">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex flex-1 items-center gap-3 text-left"
        >
          <span className="flex size-9 items-center justify-center rounded-[9px] bg-brand-accent-soft text-brand-accent">
            {icon}
          </span>
          <span className="text-[17px] font-bold tracking-tight text-foreground">{title}</span>
          {typeof count === "number" && count > 0 && (
            <span className="ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-surface3 px-1.5 text-xs font-semibold text-secondary">
              {count}
            </span>
          )}
          <span className="ml-auto text-muted-foreground">
            <ChevronDown
              size={16}
              className={cn("transition-transform duration-200", open ? "rotate-0" : "-rotate-90")}
            />
          </span>
        </button>
        {headerAction && <div className="flex shrink-0 items-center">{headerAction}</div>}
      </div>
      {open && (
        <div className="border-t border-border bg-background/35 px-4 py-3.5">{children}</div>
      )}
    </div>
  );
}
