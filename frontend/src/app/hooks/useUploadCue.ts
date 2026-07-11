"use client";

import { useCallback, useMemo, useState } from "react";
import { CAREER_AGENT_UPLOAD_DIR } from "@/app/lib/uploadFiles";
import { useChatContext } from "@/providers/ChatProvider";

// v2: dismissal now means "clicked a Files-panel upload control", no longer
// "uploaded once" — an upload from the hero card must NOT retire the dot,
// since that user still hasn't found where later uploads live.
const DISMISSED_KEY = "nr-upload-cue-dismissed-v2";

function readDismissed(): boolean {
  try {
    return typeof window !== "undefined" && localStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * First-run "upload something" guidance state.
 *
 *  - The actionable empty states (chat hero CTA, Files panel block) render
 *    whenever the user has no uploads — permanent UX, never dismissed;
 *  - the pulse dot on the Files-panel Upload button persists — through the
 *    first upload, across sessions — until the user clicks any panel upload
 *    trigger (header button, empty-state CTA, add-files tile). Finding the
 *    button is the dismissal, so post-first-upload users still get pointed
 *    at where the next resume/JD goes; cancelling the picker still counts.
 *
 * Both gate on `filesReady` so returning users never see a first-paint flash
 * while the artifact list is still loading.
 */
export function useUploadCue() {
  const { files, filesReady } = useChatContext();

  const hasUploads = useMemo(
    () => Object.keys(files).some((p) => p.startsWith(`${CAREER_AGENT_UPLOAD_DIR}/`)),
    [files]
  );

  // Lazy init is safe: this subtree mounts client-only behind the config gate,
  // so there is no SSR/hydration pass to mismatch (same as useThreadsPanel).
  const [dismissed, setDismissed] = useState(readDismissed);

  const dismissCue = useCallback(() => {
    setDismissed(true);
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      // ignore storage failures
    }
  }, []);

  return {
    hasUploads,
    showUploadCta: filesReady && !hasUploads,
    showPulseCue: filesReady && !dismissed,
    dismissCue,
  };
}
