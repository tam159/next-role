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
        <div className="border-primary/20 bg-primary/5 rounded-2xl border border-dashed px-4 py-5 text-center">
          <div className="bg-primary/10 text-primary mx-auto mb-3 flex size-10 items-center justify-center rounded-2xl">
            <Sparkles size={18} />
          </div>
          <p className="text-foreground text-base font-semibold">No tasks yet</p>
          <p className="text-muted-foreground mt-1 text-sm">
            The agent&apos;s plan will appear here as soon as work starts.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="border-primary/15 from-primary/10 via-surface to-surface overflow-hidden rounded-2xl border bg-linear-to-br p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div className="text-foreground text-sm font-semibold">Progress</div>
              <div className="flex items-center gap-2">
                <span className="text-primary text-sm font-semibold tabular-nums">{progress}%</span>
                {activeCount > 0 && (
                  <span className="border-warning/25 bg-warning/10 text-warning inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium">
                    <span className="bg-warning size-1.5 animate-pulse rounded-full" />
                    Active
                  </span>
                )}
              </div>
            </div>
            <div className="bg-muted mt-3 h-2 overflow-hidden rounded-full">
              <div
                className="bg-primary h-full rounded-full transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>

          {STATUS_ORDER.filter((s) => grouped[s].length > 0).map((status) => (
            <div key={status}>
              <h3 className="text-muted-foreground mb-2 text-xs font-semibold tracking-[0.16em] uppercase">
                {STATUS_LABEL[status]}
              </h3>
              <div className="before:bg-border relative flex flex-col gap-2 text-sm before:absolute before:top-2 before:bottom-2 before:left-[8px] before:w-px">
                {grouped[status].map((todo, idx) => (
                  <div
                    key={`${status}_${todo.id}_${idx}`}
                    className="relative grid grid-cols-[auto_1fr] gap-2"
                  >
                    <span className="bg-background relative z-10 mt-2 flex size-4 items-center justify-center rounded-full">
                      <StatusIcon status={todo.status} />
                    </span>
                    <span
                      className={cn(
                        "bg-surface/70 border-border rounded-xl border px-3 py-2 leading-relaxed wrap-break-word shadow-sm",
                        status === "in_progress" &&
                          "border-warning/30 bg-warning/10 shadow-warning/5 text-foreground",
                        status === "completed" &&
                          "text-muted-foreground bg-transparent line-through shadow-none"
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
