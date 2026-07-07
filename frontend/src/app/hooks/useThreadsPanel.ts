"use client";

import { useCallback, useState } from "react";

const PINNED_KEY = "nr-threads-pinned";

function readPinned(): boolean {
  try {
    return typeof window !== "undefined" && localStorage.getItem(PINNED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Open/pinned state for the docked, collapsible threads panel.
 *
 *  - `open` is plain UI state (deliberately not URL state — a second nuqs
 *    setter alongside `threadId` is what caused the drawer's same-tick query
 *    clobber, and panel visibility isn't worth a shareable URL);
 *  - `pinned` is a persisted preference: the panel stays open across thread
 *    selection and is restored open on the next visit;
 *  - closing (top-bar toggle or the header X) always unpins — pinned means
 *    "persistently open", so a closed panel can never be pinned.
 */
export function useThreadsPanel() {
  // Lazy init is safe: the page mounts this subtree client-only behind the
  // config gate, so there is no SSR/hydration pass to mismatch. It also means
  // pinned users get the panel open on first paint — no flash, no load slide.
  const [pinned, setPinnedState] = useState(readPinned);
  const [open, setOpen] = useState(pinned);

  const setPinned = useCallback((value: boolean) => {
    setPinnedState(value);
    try {
      localStorage.setItem(PINNED_KEY, value ? "1" : "0");
    } catch {
      // ignore storage failures
    }
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setPinned(false);
  }, [setPinned]);

  const toggle = useCallback(() => (open ? close() : setOpen(true)), [open, close]);

  const togglePin = useCallback(() => setPinned(!pinned), [pinned, setPinned]);

  /** Call after a thread is selected: unpinned panels auto-close. */
  const onThreadSelected = useCallback(() => {
    if (!pinned) setOpen(false);
  }, [pinned]);

  return { open, pinned, toggle, close, togglePin, onThreadSelected };
}
