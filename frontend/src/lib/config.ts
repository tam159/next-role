import { isAuthEnabled } from "@/lib/auth/enabled";
import { DEFAULT_AGENT_ID, isKnownAgentId } from "@/app/config/agents";

export interface StandaloneConfig {
  deploymentUrl: string;
  assistantId: string;
  /**
   * Which agent the user picked, as a LangGraph `graph_id`.
   *
   * Not pinned in auth mode like `deploymentUrl`/`assistantId` are: it only
   * chooses between graphs already registered on the same pinned deployment,
   * and it is validated against the static registry on read, so it cannot
   * redirect anything anywhere.
   */
  selectedAgentId?: string;
  langsmithApiKey?: string;
  mainAgentModel?: string;
  subagentModel?: string;
}

const CONFIG_KEY = "deep-agent-config";

export const DEFAULT_CONFIG: StandaloneConfig | null =
  process.env.NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL && process.env.NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID
    ? {
        deploymentUrl: process.env.NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL,
        assistantId: process.env.NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID,
        langsmithApiKey: process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || undefined,
      }
    : null;

export function getConfig(): StandaloneConfig | null {
  if (typeof window === "undefined") return DEFAULT_CONFIG;

  const stored = localStorage.getItem(CONFIG_KEY);
  if (!stored) return DEFAULT_CONFIG;

  let parsed: StandaloneConfig;
  try {
    parsed = JSON.parse(stored);
  } catch {
    return DEFAULT_CONFIG;
  }

  // In multi-user mode, pin the backend URL/assistant to the deployment env.
  // A stored deploymentUrl override would let injected page script redirect
  // bearer tokens to an attacker-controlled origin; model/UI prefs stay local.
  if (isAuthEnabled() && DEFAULT_CONFIG) {
    return {
      ...parsed,
      deploymentUrl: DEFAULT_CONFIG.deploymentUrl,
      assistantId: DEFAULT_CONFIG.assistantId,
    };
  }
  return parsed;
}

export function saveConfig(config: StandaloneConfig): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}

/**
 * The graph id to talk to: the user's pick, else the deployment's default.
 *
 * Unknown ids fall back rather than throwing — a stored pick can outlive the
 * graph it named (a renamed or removed agent), and the deployment default is
 * always a safe landing place.
 */
export function getEffectiveAgentId(config: StandaloneConfig | null): string {
  if (isKnownAgentId(config?.selectedAgentId)) return config.selectedAgentId;
  if (isKnownAgentId(config?.assistantId)) return config.assistantId;
  return config?.assistantId || DEFAULT_AGENT_ID;
}
