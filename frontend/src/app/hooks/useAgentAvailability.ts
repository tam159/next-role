"use client";

import useSWR from "swr";
import { getConfig } from "@/lib/config";
import { authedFetch } from "@/lib/auth/token";

/**
 * Which agents this caller may actually run.
 *
 * The backend enforces access inside each agent; this only tells the picker
 * what to disable, so a user sees a greyed-out entry with a reason instead of
 * starting a conversation that ends in a refusal.
 *
 * Failure is deliberately permissive: a deployment without the endpoint (or an
 * offline backend) should still let the picker work, since the agent itself
 * refuses anyone it must.
 */
export function useAgentAvailability(): Record<string, boolean> {
  const deploymentUrl = getConfig()?.deploymentUrl;

  const { data } = useSWR(
    deploymentUrl ? [deploymentUrl, "agents-available"] : null,
    async ([url]) => {
      const response = await authedFetch(`${url.replace(/\/+$/, "")}/agents/available`);
      if (!response.ok) throw new Error(`agents/available: ${response.status}`);
      const body = (await response.json()) as {
        agents?: { graph_id: string; allowed: boolean }[];
      };
      return Object.fromEntries((body.agents ?? []).map((a) => [a.graph_id, a.allowed]));
    },
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );

  return data ?? {};
}
