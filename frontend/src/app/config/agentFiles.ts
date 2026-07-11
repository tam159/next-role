export type AgentFileSources = {
  store?: {
    namespacePrefix: readonly string[];
    pathPrefixes: readonly string[];
  };
  /**
   * Artifact areas served by the backend files API (`/files/*` on the
   * deployment URL), stored in S3-compatible object storage. Paths are
   * virtual (`/upload/cv.pdf`) — the same currency the agent uses; the
   * frontend never sees storage keys or disk layout.
   */
  artifacts?: {
    pathPrefixes: readonly string[];
  };
};

export const AGENT_FILE_SOURCES: Record<string, AgentFileSources> = {
  career_agent: {
    store: {
      namespacePrefix: ["career_agent"],
      pathPrefixes: ["/memory/", "/processed/", "/research/", "/interview_coach/", "/workspace/"],
    },
    artifacts: {
      pathPrefixes: ["/upload/", "/tailored_resume/", "/interview_battlecard/"],
    },
  },
};
