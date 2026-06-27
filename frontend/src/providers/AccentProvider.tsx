"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

/**
 * User-selectable accent. next-themes only manages light/dark, so accent gets
 * its own lightweight mechanism: a `data-accent` attribute on <html> (consumed
 * by the `[data-accent="..."]` blocks in globals.css) persisted under
 * `nr-accent`. No attribute = indigo (the default in CSS), so SSR is correct
 * before JS runs; an inline pre-paint script in layout.tsx restores a saved
 * non-default accent before first paint to avoid a flash.
 */
export const ACCENTS = ["indigo", "blue", "emerald", "coral"] as const;
export type Accent = (typeof ACCENTS)[number];
export const DEFAULT_ACCENT: Accent = "indigo";
export const ACCENT_STORAGE_KEY = "nr-accent";

function isAccent(value: unknown): value is Accent {
  return typeof value === "string" && (ACCENTS as readonly string[]).includes(value);
}

interface AccentContextValue {
  accent: Accent;
  setAccent: (accent: Accent) => void;
}

const AccentContext = createContext<AccentContextValue | null>(null);

export function AccentProvider({ children }: { children: React.ReactNode }) {
  const [accent, setAccentState] = useState<Accent>(DEFAULT_ACCENT);

  // Sync React state to whatever the pre-paint script already applied. Reading
  // localStorage in a useState initializer would diverge from the SSR render
  // (server has no storage) and warn on hydration, so this mount effect is the
  // SSR-safe way to adopt the persisted accent.
  useEffect(() => {
    const stored = window.localStorage.getItem(ACCENT_STORAGE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot hydration sync
    if (isAccent(stored)) setAccentState(stored);
  }, []);

  const setAccent = useCallback((next: Accent) => {
    setAccentState(next);
    document.documentElement.setAttribute("data-accent", next);
    try {
      window.localStorage.setItem(ACCENT_STORAGE_KEY, next);
    } catch {
      // ignore storage failures (private mode, quota)
    }
  }, []);

  return <AccentContext.Provider value={{ accent, setAccent }}>{children}</AccentContext.Provider>;
}

export function useAccent(): AccentContextValue {
  const ctx = useContext(AccentContext);
  if (!ctx) throw new Error("useAccent must be used within AccentProvider");
  return ctx;
}
