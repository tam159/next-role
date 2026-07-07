"use client";

import { useCallback, useState } from "react";

interface UseAutoCollapseOptions {
  /**
   * Pins the unit open and makes `toggle` a no-op. Used while a HITL
   * interrupt is pending — collapsing would hide the approval UI that
   * unblocks the run.
   */
  forceExpanded?: boolean;
}

/**
 * Expand/collapse state for one unit of agent activity (a tool-call group or
 * a subagent card):
 *
 *  - mounts expanded when the unit is running, collapsed when it is already
 *    terminal (history reload / older messages render as compact summaries
 *    with no entry animation);
 *  - auto-collapses once on the running → terminal transition, and re-expands
 *    on terminal → running (interrupt resume, run reattach);
 *  - a manual toggle wins permanently: after the user touches the control the
 *    hook never auto-drives that unit again.
 */
export function useAutoCollapse(
  isRunning: boolean,
  { forceExpanded = false }: UseAutoCollapseOptions = {}
): { isExpanded: boolean; toggle: () => void } {
  const [expanded, setExpanded] = useState(isRunning);
  const [userOverride, setUserOverride] = useState(false);

  // Render-phase adjustment rather than an effect: it keys off the derived
  // boolean alone (immune to memoized message identities) and repaints in the
  // same frame as the transition.
  const [prevRunning, setPrevRunning] = useState(isRunning);
  if (prevRunning !== isRunning) {
    setPrevRunning(isRunning);
    if (!userOverride) setExpanded(isRunning);
  }

  const toggle = useCallback(() => {
    if (forceExpanded) return;
    setUserOverride(true);
    setExpanded((prev) => !prev);
  }, [forceExpanded]);

  return { isExpanded: forceExpanded || expanded, toggle };
}
