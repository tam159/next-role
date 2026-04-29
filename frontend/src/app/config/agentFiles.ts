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
};
