"use client";

import { useCallback, useRef } from "react";

/**
 * Pointerdown-first activation (the ToolCallBox pattern): `pointerdown` fires
 * before the OS click-eating window during heavy main-thread work (Safari,
 * mid-stream), `click` covers keyboard activation, and a timestamp dedupes the
 * double fire of a single tap.
 */
export function usePointerToggle(onToggle: () => void) {
  const lastPointerToggleRef = useRef(0);
  const onPointerDown = useCallback(() => {
    lastPointerToggleRef.current = performance.now();
    onToggle();
  }, [onToggle]);
  const onClick = useCallback(() => {
    if (performance.now() - lastPointerToggleRef.current < 300) return;
    onToggle();
  }, [onToggle]);
  return { onPointerDown, onClick };
}
