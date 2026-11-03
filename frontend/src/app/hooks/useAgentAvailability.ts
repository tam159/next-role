"use client";

import useSWR from "swr";
import { getConfig } from "@/lib/config";
import { authedFetch } from "@/lib/auth/token";
import { AGENT_IDS, type AgentAccess } from "@/app/config/agents";

interface AgentsAvailableResponse {
  agents?: { graph_id: string; allowed: boolean }[];
}

/**
 * Map a `GET /agents/available` body to each picker agent's access.
 *
 * The backend lists only the graphs it registers, so a picker agent missing
 * from the list is one this deployment does not run (production serves only
 * the career agent). Graphs the picker does not offer are ignored.
 */
export function toAgentAccess(body: AgentsAvailableResponse): Record<string, AgentAccess> {
  const allowed = new Map((body.agents ?? []).map((a) => [a.graph_id, a.allowed]));
  return Object.fromEntries(
    AGENT_IDS.map((graphId): [string, AgentAccess] => {
      if (!allowed.has(graphId)) return [graphId, "not_deployed"];
      return [graphId, allowed.get(graphId) ? "allowed" : "admin_only"];
    })
  );
}

/**
 * Which agents this caller may actually run.
 *
 * The backend enforces access inside each agent; this only tells the picker
 * what to disable, so a user sees a greyed-out entry with a reason instead of
 * starting a conversation that ends in a refusal.
 *
 * Failure is deliberately permissive: a deployment without the endpoint (or an
 * offline backend) yields `{}`, which `agentAccess` reads as allowed, since the
 * agent itself refuses anyone it must.
 */
export function useAgentAvailability(): Record<string, AgentAccess> {
  const deploymentUrl = getConfig()?.deploymentUrl;

  const { data } = useSWR(
    deploymentUrl ? [deploymentUrl, "agents-available"] : null,
    async ([url]) => {
      const response = await authedFetch(`${url.replace(/\/+$/, "")}/agents/available`);
      if (!response.ok) throw new Error(`agents/available: ${response.status}`);
      return toAgentAccess((await response.json()) as AgentsAvailableResponse);
    },
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );

  return data ?? EMPTY;
}

// Stable identity while loading, so effects keyed on availability don't rerun.
const EMPTY: Record<string, AgentAccess> = {};
