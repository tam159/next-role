import { isAuthEnabled } from "@/lib/auth/enabled";

export interface StandaloneConfig {
  deploymentUrl: string;
  assistantId: string;
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
