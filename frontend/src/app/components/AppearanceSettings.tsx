"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { ACCENTS, useAccent, type Accent } from "@/providers/AccentProvider";

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

// Swatch preview colors (light-theme accent solids).
const ACCENT_SWATCH: Record<Accent, string> = {
  indigo: "#5B5BD6",
  blue: "#2563EB",
  emerald: "#0E9F6E",
  coral: "#E0623C",
};

const EYEBROW = "text-[11px] font-bold uppercase tracking-[0.08em] text-tertiary";

export function AppearanceSettings() {
  const { theme, setTheme } = useTheme();
  const { accent, setAccent } = useAccent();

  // next-themes resolves `theme` only after mount; guard to avoid a hydration
  // mismatch on the active-state highlight.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const activeTheme = mounted ? theme : undefined;

  return (
    <div className="grid gap-4">
      <span className={EYEBROW}>Appearance</span>

      {/* Theme */}
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-primary">Theme</span>
        <div className="inline-flex items-center gap-1 rounded-full border border-primary bg-surface3 p-1">
          {THEME_OPTIONS.map(({ value, label, icon: Icon }) => {
            const active = activeTheme === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setTheme(value)}
                aria-pressed={active}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                  active
                    ? "bg-surface-raised text-primary shadow-sm"
                    : "text-secondary hover:text-primary"
                )}
              >
                <Icon className="size-3.5" />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Accent */}
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-primary">Accent</span>
        <div className="flex items-center gap-2">
          {ACCENTS.map((value) => {
            const active = accent === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setAccent(value)}
                aria-label={`${value} accent`}
                aria-pressed={active}
                title={value[0].toUpperCase() + value.slice(1)}
                className={cn(
                  "grid size-8 place-items-center rounded-[9px] ring-offset-2 ring-offset-surface-raised transition-all",
                  active ? "ring-2 ring-primary" : "hover:scale-105"
                )}
                style={{ backgroundColor: ACCENT_SWATCH[value] }}
              >
                {active && <Check className="size-4 text-white" strokeWidth={3} />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
