export type AgentFileSources = {
  store?: {
    namespacePrefix: readonly string[];
    pathPrefixes: readonly string[];
  };
  disk?: {
    root: string;
    includeDirs: readonly string[];
  };
};

export const AGENT_FILE_SOURCES: Record<string, AgentFileSources> = {
  career_agent: {
    store: {
      namespacePrefix: ["career_agent"],
      pathPrefixes: ["/memory/", "/processed/", "/research/", "/interview_coach/", "/workspace/"],
    },
    disk: {
      root: "backend/agents/career_agent",
      includeDirs: ["upload", "tailored_resume", "interview_battlecard"],
    },
  },
};
