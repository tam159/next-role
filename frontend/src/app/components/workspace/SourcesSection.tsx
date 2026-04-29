"use client";

import React from "react";
import { Globe } from "lucide-react";
import type { Source } from "@/app/types/types";
import { WorkspaceCard } from "@/app/components/workspace/WorkspaceCard";

interface SourcesSectionProps {
  sources: Source[];
  open: boolean;
  onToggle: () => void;
}

function hostFromUrl(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
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
        <ul className="flex flex-col gap-2">
          {sources.map((source) => (
            <li key={source.id}>
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="bg-surface/60 hover:border-primary/20 block rounded-xl border border-transparent px-3 py-2 transition-colors hover:bg-accent/60"
              >
                <p className="line-clamp-2 text-[15px] leading-snug text-foreground">
                  {source.title}
                </p>
                <p className="mt-0.5 truncate text-sm text-muted-foreground">
                  {hostFromUrl(source.url)}
                </p>
              </a>
            </li>
          ))}
        </ul>
      )}
    </WorkspaceCard>
  );
}
