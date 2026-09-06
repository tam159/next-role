import { BarChart3, Compass, type LucideIcon } from "lucide-react";

/**
 * The agents this deployment can serve, keyed by LangGraph `graph_id`.
 *
 * The backend is the authority on which graphs exist (`LANGSERVE_GRAPHS`) and
 * who may run them (`GET /agents/available`). This registry only carries what
 * the backend has no opinion about: how each agent is named and coloured in the
 * picker. An id that is not listed here is not offered.
 */
export interface AgentMeta {
  graphId: string;
  name: string;
  /** One line, shown under the name in the picker. */
  description: string;
  /** CSS colour for the agent's dot. Career uses the live accent token. */
  color: string;
  icon: LucideIcon;
}

export const CAREER_AGENT_ID = "career_agent";
export const ANALYTICS_AGENT_ID = "analytics_agent";

export const AGENTS: Record<string, AgentMeta> = {
  [CAREER_AGENT_ID]: {
    graphId: CAREER_AGENT_ID,
    name: "Career Agent",
    description: "Orchestrates your end-to-end prep",
    color: "var(--brand-accent)",
    icon: Compass,
  },
  [ANALYTICS_AGENT_ID]: {
    graphId: ANALYTICS_AGENT_ID,
    name: "Analytics Agent",
    description: "Answers questions about product usage and cost",
    color: "#5b5bd6",
    icon: BarChart3,
  },
};

/** Graph ids in picker order. */
export const AGENT_IDS = [CAREER_AGENT_ID, ANALYTICS_AGENT_ID];

export const DEFAULT_AGENT_ID = CAREER_AGENT_ID;

export function isKnownAgentId(value: string | null | undefined): value is string {
  return typeof value === "string" && value in AGENTS;
}

/**
 * Display metadata for a graph id, falling back to the id itself.
 *
 * A deployment can register a graph this build has never heard of; showing its
 * id beats showing nothing.
 */
export function agentMeta(graphId: string | null | undefined): AgentMeta {
  if (isKnownAgentId(graphId)) return AGENTS[graphId];
  return {
    graphId: graphId ?? DEFAULT_AGENT_ID,
    name: graphId ?? "Assistant",
    description: "",
    color: "var(--brand-accent)",
    icon: Compass,
  };
}

/**
 * The specialists the Career Agent delegates to via the `task` tool.
 *
 * Informational, not selectable: they run inside a career-agent conversation,
 * never as a top-level agent.
 */
export const CAREER_SUBAGENTS = [
  { name: "Resume Tailor", description: "Rewrites your resume against the JD", color: "#0e9f6e" },
  { name: "Interview Coach", description: "STAR stories, round-by-round", color: "#2563eb" },
  { name: "Company Research", description: "Live recon on the company & role", color: "#d9785a" },
];
