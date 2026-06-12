"use client";

import React, { useMemo } from "react";
import { CheckCircle, Circle, Clock, ListTodo, Sparkles } from "lucide-react";
import type { TodoItem } from "@/app/types/types";
import { cn } from "@/lib/utils";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";

const STATUS_LABEL: Record<TodoItem["status"], string> = {
  in_progress: "In Progress",
  pending: "Pending",
  completed: "Completed",
};

const STATUS_ORDER: TodoItem["status"][] = ["in_progress", "pending", "completed"];

function StatusIcon({ status, className }: { status: TodoItem["status"]; className?: string }) {
  switch (status) {
    case "completed":
      return <CheckCircle size={14} className={cn("text-success/80", className)} />;
    case "in_progress":
      return <Clock size={14} className={cn("text-warning/80", className)} />;
    default:
      return <Circle size={12} className={cn("text-tertiary/70", className)} />;
  }
}

interface PlanSectionProps {
  todos: TodoItem[];
  open: boolean;
  onToggle: () => void;
}

export function PlanSection({ todos, open, onToggle }: PlanSectionProps) {
  const { grouped, activeCount, progress } = useMemo(() => {
    const out: Record<TodoItem["status"], TodoItem[]> = {
      in_progress: [],
      pending: [],
      completed: [],
    };
    for (const t of todos) out[t.status].push(t);

    const completed = out.completed.length;
    const total = todos.length;
    return {
      grouped: out,
      activeCount: out.in_progress.length,
      progress: total > 0 ? Math.round((completed / total) * 100) : 0,
    };
  }, [todos]);

  return (
    <WorkspaceCard
      icon={<ListTodo size={18} />}
      title="Plan"
      count={todos.length}
      open={open}
      onToggle={onToggle}
    >
      {todos.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-primary/20 bg-primary/5 px-4 py-5 text-center">
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <Sparkles size={18} />
          </div>
          <p className="text-base font-semibold text-foreground">No tasks yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            The agent&apos;s plan will appear here as soon as work starts.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="overflow-hidden rounded-2xl border border-primary/15 bg-linear-to-br from-primary/10 via-surface to-surface p-4 shadow-xs">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-semibold text-foreground">Progress</div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-primary tabular-nums">{progress}%</span>
                {activeCount > 0 && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-warning/25 bg-warning/10 px-2.5 py-1 text-xs font-medium text-warning">
                    <span className="size-1.5 animate-pulse rounded-full bg-warning" />
                    Active
                  </span>
                )}
              </div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>

          {STATUS_ORDER.filter((s) => grouped[s].length > 0).map((status) => (
            <div key={status}>
              <h3 className="mb-2 text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
                {STATUS_LABEL[status]}
              </h3>
              <div className="relative flex flex-col gap-2 text-sm before:absolute before:top-2 before:bottom-2 before:left-[8px] before:w-px before:bg-border">
                {grouped[status].map((todo, idx) => (
                  <div
                    key={`${status}_${todo.id}_${idx}`}
                    className="relative grid grid-cols-[auto_1fr] gap-2"
                  >
                    <span className="relative z-10 mt-2 flex size-4 items-center justify-center rounded-full bg-background">
                      <StatusIcon status={todo.status} />
                    </span>
                    <span
                      className={cn(
                        "rounded-xl border border-border bg-surface/70 px-3 py-2 leading-relaxed wrap-break-word shadow-xs",
                        status === "in_progress" &&
                          "border-warning/30 bg-warning/10 text-foreground shadow-warning/5",
                        status === "completed" &&
                          "bg-transparent text-muted-foreground line-through shadow-none"
                      )}
                    >
                      {todo.content}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </WorkspaceCard>
  );
}
