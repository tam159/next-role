"use client";

import React from "react";
import { Globe, ExternalLink } from "lucide-react";
import type { Source } from "@/app/types/types";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";

interface SourcesSectionProps {
  sources: Source[];
  open: boolean;
  onToggle: () => void;
}

function hostFromUrl(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function letterFor(host: string): string {
  if (host.includes("linkedin")) return "in";
  const m = host.match(/[a-z0-9]/i);
  return (m?.[0] ?? "•").toUpperCase();
}

export function SourcesSection({ sources, open, onToggle }: SourcesSectionProps) {
  return (
    <WorkspaceCard
      icon={<Globe size={18} />}
      title="Sources"
      count={sources.length}
      open={open}
      onToggle={onToggle}
    >
      {sources.length === 0 ? (
        <p className="py-2 text-sm text-muted-foreground">No sources yet</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {sources.map((source) => {
            const host = hostFromUrl(source.url);
            return (
              <li key={source.id}>
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-center gap-3 rounded-xl border border-transparent px-2.5 py-2 transition-colors hover:border-brand-strong/25 hover:bg-surface3/70"
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface3 text-xs font-bold text-secondary">
                    {letterFor(host)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm leading-snug font-medium text-foreground">
                      {source.title}
                    </span>
                    <span className="block truncate text-xs text-tertiary">{host}</span>
                  </span>
                  <ExternalLink className="size-3.5 shrink-0 text-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </WorkspaceCard>
  );
}
