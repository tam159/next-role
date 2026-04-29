"use client";

import React from "react";
import { Button } from "@/components/ui/button";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Loader2,
} from "lucide-react";
import type { SubAgent } from "@/app/types/types";
import { cn } from "@/lib/utils";

interface SubAgentIndicatorProps {
  subAgent: SubAgent;
  onClick: () => void;
  isExpanded?: boolean;
}

function getSubAgentStatusMeta(status: SubAgent["status"]) {
  switch (status) {
    case "active":
      return {
        label: "Running",
        icon: Loader2,
        className: "border-primary/25 bg-primary/10 text-primary",
        iconClassName: "animate-spin",
      };
    case "completed":
      return {
        label: "Complete",
        icon: CheckCircle2,
        className: "border-success/25 bg-success/10 text-success",
        iconClassName: "",
      };
    case "error":
      return {
        label: "Failed",
        icon: AlertCircle,
        className: "border-destructive/30 bg-destructive/10 text-destructive",
        iconClassName: "",
      };
    default:
      return {
        label: "Queued",
        icon: Clock3,
        className: "border-muted bg-muted/60 text-muted-foreground",
        iconClassName: "",
      };
  }
}

export const SubAgentIndicator = React.memo<SubAgentIndicatorProps>(
  ({ subAgent, onClick, isExpanded = true }) => {
    const statusMeta = getSubAgentStatusMeta(subAgent.status);
    const StatusIcon = statusMeta.icon;

    return (
      <div className="hover:border-primary/25 w-fit max-w-[70vw] overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-sm outline-none transition-colors">
        <Button
          variant="ghost"
          size="sm"
          onClick={onClick}
          className="flex h-auto w-full items-center justify-between gap-3 border-none px-3 py-3 text-left shadow-none outline-none transition-colors duration-200 hover:bg-transparent"
        >
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <span className="bg-primary/10 flex size-8 shrink-0 items-center justify-center rounded-xl text-primary">
              <Bot size={16} />
            </span>
            <div className="min-w-0">
              <span className="block truncate font-sans text-[15px] font-bold leading-[140%] tracking-[-0.4px] text-foreground">
                {subAgent.subAgentName}
              </span>
              <span
                className={cn(
                  "mt-1 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                  statusMeta.className
                )}
              >
                <StatusIcon size={12} className={statusMeta.iconClassName} />
                {statusMeta.label}
              </span>
            </div>
          </div>
          <div className="shrink-0">
            {isExpanded ? (
              <ChevronUp size={14} className="text-muted-foreground" />
            ) : (
              <ChevronDown size={14} className="text-muted-foreground" />
            )}
          </div>
        </Button>
      </div>
    );
  }
);

SubAgentIndicator.displayName = "SubAgentIndicator";
