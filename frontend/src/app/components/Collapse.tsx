"use client";

import React, { type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface CollapseProps {
  isExpanded: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * Height-animated disclosure via the CSS grid-rows trick. Children stay
 * mounted while collapsed (0fr row + `inert`) so streaming subscriptions and
 * nested expand state survive collapse cycles, and a unit that mounts
 * already-collapsed renders at 0fr with no entry animation.
 */
export function Collapse({ isExpanded, children, className }: CollapseProps) {
  return (
    <div
      className={cn(
        "grid transition-[grid-template-rows] duration-300 ease-in-out motion-reduce:transition-none",
        isExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        className
      )}
    >
      <div className="min-h-0 overflow-hidden" inert={!isExpanded}>
        {children}
      </div>
    </div>
  );
}
