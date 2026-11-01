"use client";

import { useEffect, useRef } from "react";
import useSWR from "swr";
import { getConfig } from "@/lib/config";
import { authedFetch } from "@/lib/auth/token";

/** The caller's usage quota, as reported by the backend's `GET /usage`. */
export interface UsageStatus {
  /** False when the deployment sets no `QUOTA_*` limits (local development). */
  enabled: boolean;
  /** New runs are refused right now. */
  blocked?: boolean;
  reason?: "runs_day" | "runs_week" | "usd_day" | "usd_week" | "global_usd_day" | null;
  /** Server-written explanation, with the reset time in UTC. */
  message?: string | null;
  /** ISO timestamp when the blocking window frees up. */
  resets_at?: string | null;
  /** Runs left in the tighter of the daily/weekly windows; null without run limits. */
  runs_left_today?: number | null;
}

const BLOCKED_COPY: Record<NonNullable<UsageStatus["reason"]>, string> = {
  runs_day: "You've used all your runs for the last 24 hours.",
  runs_week: "You've used all your runs for the last 7 days.",
  usd_day: "You've used your usage allowance for the last 24 hours.",
  usd_week: "You've used your usage allowance for the last 7 days.",
  global_usd_day: "NextRole has reached its shared daily capacity.",
};

/** One-line explanation for a blocked composer, with the reset time in local time. */
export function blockedMessage(usage: UsageStatus): string {
  const base =
    (usage.reason && BLOCKED_COPY[usage.reason]) || usage.message || "Usage limit reached.";
  if (!usage.resets_at) return base;
  const at = new Date(usage.resets_at);
  const sameDay = at.toDateString() === new Date().toDateString();
  const when = at.toLocaleString(undefined, {
    ...(sameDay ? {} : { weekday: "short" }),
    hour: "numeric",
    minute: "2-digit",
  });
  return `${base} You can start a new run after ${when}.`;
}

/**
 * The server's explanation when run creation was refused over quota, or null
 * for any other failure. Two shapes reach the client:
 *  - REST run creation: the SDK's `HTTPError` (`status` 429, raw body in
 *    `text`, wrapped by the agent server as `{detail}`);
 *  - the v2 stream protocol's `run.start` command: a `ProtocolError` whose
 *    message is the server exception's text, `"429: <detail>"`.
 */
export function quotaRefusalMessage(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  const { status, text, message } = error as {
    status?: unknown;
    text?: unknown;
    message?: unknown;
  };
  if (status !== 429) {
    const match = typeof message === "string" ? /^429:\s*([\s\S]+)$/.exec(message) : null;
    return match ? match[1].trim() : null;
  }
  if (typeof text === "string" && text) {
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string") return body.detail;
    } catch {
      return text;
    }
  }
  return "Usage limit reached. Try again later.";
}

/**
 * Quota state for the composer. Refetches whenever a run finishes (`isLoading`
 * falls back to false), so the remaining count stays current.
 *
 * Failure is deliberately permissive — like `useAgentAvailability`: an older
 * backend without the endpoint, or a network blip, must never lock the
 * composer. The server still refuses an over-quota run with a 429.
 */
export function useUsage(isLoading: boolean): {
  usage: UsageStatus | null;
  refresh: () => void;
} {
  const deploymentUrl = getConfig()?.deploymentUrl;

  const { data, mutate } = useSWR(
    deploymentUrl ? [deploymentUrl, "usage"] : null,
    async ([url]) => {
      const response = await authedFetch(`${url.replace(/\/+$/, "")}/usage`);
      if (!response.ok) throw new Error(`usage: ${response.status}`);
      return (await response.json()) as UsageStatus;
    },
    { revalidateOnFocus: true, shouldRetryOnError: false }
  );

  const wasLoading = useRef(isLoading);
  useEffect(() => {
    if (wasLoading.current && !isLoading) void mutate();
    wasLoading.current = isLoading;
  }, [isLoading, mutate]);

  return {
    usage: data?.enabled ? data : null,
    refresh: () => void mutate(),
  };
}
