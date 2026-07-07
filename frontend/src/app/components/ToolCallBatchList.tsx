"use client";

import React, { type ReactNode } from "react";
import { Split } from "lucide-react";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import type { ToolCall } from "@/app/types/types";

interface ToolCallBatchListProps {
  /** Non-empty batches of non-empty tool-call runs; one batch = one LLM step. */
  batches: ToolCall[][];
  /** Row renderer; defaults to a plain ToolCallBox. Must key its element. */
  renderToolCall?: (toolCall: ToolCall) => ReactNode;
}

const defaultRenderToolCall = (toolCall: ToolCall) => (
  <ToolCallBox key={toolCall.id} toolCall={toolCall} />
);

/**
 * A column of tool-call rows clustered by issuing step, on the parent's
 * timeline rail. Steps that issued several calls at once open with an
 * "N in parallel" micro-label and close with a light break, so where the
 * simultaneous cluster ends stays legible against the next sequential call.
 */
export function ToolCallBatchList({ batches, renderToolCall }: ToolCallBatchListProps) {
  const renderItem = renderToolCall ?? defaultRenderToolCall;
  return (
    <div className="flex flex-col gap-2.5">
      {batches.map((batch, index) => (
        <div key={batch[0].id} className="flex flex-col gap-1.5">
          {batch.length > 1 && (
            <div className="grid grid-cols-[26px_minmax(0,1fr)] gap-3">
              <span aria-hidden />
              <span className="flex items-center gap-1.5 px-2.5 text-[10.5px] font-semibold tracking-wider text-tertiary uppercase">
                <Split size={11} />
                {batch.length} in parallel
              </span>
            </div>
          )}
          {batch.map(renderItem)}
          {batch.length > 1 && index < batches.length - 1 && (
            <div aria-hidden className="grid grid-cols-[26px_minmax(0,1fr)] gap-3">
              <span />
              <span className="mx-2.5 mt-0.5 h-px bg-border2" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
