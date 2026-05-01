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
  content_builder: {
    store: {
      namespacePrefix: ["content_builder", "filesystem"],
      pathPrefixes: ["/memories/", "/workspace/"],
    },
    disk: {
      root: "app/content_builder",
      includeDirs: ["blogs", "tweets", "linkedin", "research", "public"],
    },
  },
  career_agent: {
    store: {
      namespacePrefix: ["career_agent"],
      pathPrefixes: [
        "/memory/",
        "/upload/processed/",
        "/research/",
        "/interview_prep/",
        "/large_tool_results/",
        "/workspace/",
      ],
    },
    disk: {
      root: "backend/app/career_agent",
      includeDirs: ["upload/raw"],
    },
  },
};
