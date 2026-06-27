"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Light/dark theming via next-themes. `attribute="class"` writes `class="dark"`
 * on <html> (matched by Tailwind's `darkMode: ["class"]` and the `.dark` token
 * block in globals.css). `defaultTheme="system"` + `enableSystem` preserves the
 * pre-redesign behavior of following the OS until the user explicitly picks a
 * theme; the choice persists in localStorage (next-themes key `theme`).
 *
 * Accent color is orthogonal and handled separately by AccentProvider.
 */
export function ThemeProvider({ children, ...props }: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
