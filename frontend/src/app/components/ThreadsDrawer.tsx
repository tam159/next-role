"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

/**
 * Left slide-over housing the ThreadList. Built on Radix Dialog primitives (not
 * the centered DialogContent) for an edge-anchored panel — it brings focus
 * trap, ESC-to-close, overlay click-out, and scroll lock for free.
 */
export function ThreadsDrawer({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-[var(--scrim)]",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0"
          )}
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex w-[330px] max-w-[88vw] flex-col bg-surface shadow-[var(--shadow-lg)] outline-hidden",
            "data-[state=open]:animate-in data-[state=open]:duration-200 data-[state=open]:slide-in-from-left",
            "data-[state=closed]:animate-out data-[state=closed]:duration-150 data-[state=closed]:slide-out-to-left"
          )}
        >
          <DialogPrimitive.Title className="sr-only">Threads</DialogPrimitive.Title>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
