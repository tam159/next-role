const DEPLOYMENT_URL = "http://localhost:2024";
const ASSISTANT_ID = "career_agent";

/**
 * DEFAULT_CONFIG is computed from process.env at import time, so every test
 * stubs the env first, then dynamically imports a fresh copy of the module.
 */
async function loadConfig() {
  return await import("@/lib/config");
}

function stubRequiredEnv() {
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", DEPLOYMENT_URL);
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", ASSISTANT_ID);
}

beforeEach(() => {
  vi.resetModules();
  // Start from a known-clean slate regardless of the host shell's env.
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", undefined);
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", undefined);
  vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", undefined);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("DEFAULT_CONFIG", () => {
  it("is built from env when both required vars are set", async () => {
    stubRequiredEnv();
    vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", "");
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG).toEqual({
      deploymentUrl: DEPLOYMENT_URL,
      assistantId: ASSISTANT_ID,
      langsmithApiKey: undefined,
    });
  });

  it("coerces an empty NEXT_PUBLIC_LANGSMITH_API_KEY to undefined", async () => {
    stubRequiredEnv();
    vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", "");
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG?.langsmithApiKey).toBeUndefined();
  });

  it("includes the langsmith key when it is set", async () => {
    stubRequiredEnv();
    vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", "ls-secret");
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG?.langsmithApiKey).toBe("ls-secret");
  });

  it("is null when only the deployment URL is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", DEPLOYMENT_URL);
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG).toBeNull();
  });

  it("is null when only the assistant id is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", ASSISTANT_ID);
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG).toBeNull();
  });

  it("is null when neither required var is set", async () => {
    const { DEFAULT_CONFIG } = await loadConfig();
    expect(DEFAULT_CONFIG).toBeNull();
  });
});

describe("getConfig (SSR / node, no window)", () => {
  it("returns DEFAULT_CONFIG when it is populated", async () => {
    stubRequiredEnv();
    const mod = await loadConfig();
    expect(mod.getConfig()).toBe(mod.DEFAULT_CONFIG);
    expect(mod.getConfig()).toEqual({
      deploymentUrl: DEPLOYMENT_URL,
      assistantId: ASSISTANT_ID,
      langsmithApiKey: undefined,
    });
  });

  it("returns null when DEFAULT_CONFIG is null", async () => {
    const mod = await loadConfig();
    expect(mod.getConfig()).toBeNull();
  });
});

describe("saveConfig (SSR / node, no window)", () => {
  it("is a no-op that does not throw", async () => {
    const { saveConfig } = await loadConfig();
    expect(() =>
      saveConfig({ deploymentUrl: "http://example.com", assistantId: "agent" })
    ).not.toThrow();
  });
});
