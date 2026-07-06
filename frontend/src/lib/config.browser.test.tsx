/**
 * DEFAULT_CONFIG is computed from process.env at module-evaluation time, so every
 * test loads a fresh copy of the module (vi.resetModules + dynamic import) with the
 * relevant NEXT_PUBLIC vars stubbed. Runs in jsdom (real localStorage); no JSX.
 */
const CONFIG_KEY = "deep-agent-config";

type ConfigModule = typeof import("@/lib/config");

async function loadConfigModule(
  env: { deploymentUrl?: string; assistantId?: string; langsmithApiKey?: string } = {}
): Promise<ConfigModule> {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL", env.deploymentUrl);
  vi.stubEnv("NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID", env.assistantId);
  vi.stubEnv("NEXT_PUBLIC_LANGSMITH_API_KEY", env.langsmithApiKey);
  return import("@/lib/config");
}

const ENV_DEFAULTS = { deploymentUrl: "http://env-host:2024", assistantId: "env-assistant" };

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("DEFAULT_CONFIG", () => {
  it("is null when the NEXT_PUBLIC deployment env vars are unset", async () => {
    const mod = await loadConfigModule();
    expect(mod.DEFAULT_CONFIG).toBeNull();
  });

  it("is null when only one of the two required env vars is set", async () => {
    const mod = await loadConfigModule({ deploymentUrl: "http://env-host:2024" });
    expect(mod.DEFAULT_CONFIG).toBeNull();
  });

  it("derives from env when both deployment url and assistant id are set", async () => {
    const mod = await loadConfigModule({ ...ENV_DEFAULTS, langsmithApiKey: "sk-env" });
    expect(mod.DEFAULT_CONFIG).toEqual({
      deploymentUrl: "http://env-host:2024",
      assistantId: "env-assistant",
      langsmithApiKey: "sk-env",
    });
  });

  it("normalizes an empty langsmith key to undefined", async () => {
    const mod = await loadConfigModule({ ...ENV_DEFAULTS, langsmithApiKey: "" });
    expect(mod.DEFAULT_CONFIG).toEqual({
      deploymentUrl: "http://env-host:2024",
      assistantId: "env-assistant",
      langsmithApiKey: undefined,
    });
  });
});

describe("getConfig", () => {
  it("returns null with nothing stored and no env defaults", async () => {
    const mod = await loadConfigModule();
    expect(mod.getConfig()).toBeNull();
  });

  it("returns DEFAULT_CONFIG (same object) with nothing stored", async () => {
    const mod = await loadConfigModule(ENV_DEFAULTS);
    expect(mod.getConfig()).toBe(mod.DEFAULT_CONFIG);
  });

  it("returns valid stored JSON, which wins over the env default", async () => {
    const stored = {
      deploymentUrl: "http://stored-host:8123",
      assistantId: "stored-assistant",
      mainAgentModel: "model-big",
    };
    window.localStorage.setItem(CONFIG_KEY, JSON.stringify(stored));
    const mod = await loadConfigModule(ENV_DEFAULTS);
    expect(mod.getConfig()).toEqual(stored);
  });

  it("falls back to DEFAULT_CONFIG when the stored JSON is corrupted", async () => {
    window.localStorage.setItem(CONFIG_KEY, "{definitely not json!!");
    const mod = await loadConfigModule(ENV_DEFAULTS);
    expect(mod.getConfig()).toBe(mod.DEFAULT_CONFIG);
    expect(mod.getConfig()).toEqual({
      deploymentUrl: "http://env-host:2024",
      assistantId: "env-assistant",
      langsmithApiKey: undefined,
    });
  });
});

describe("saveConfig", () => {
  it("writes JSON under the storage key and round-trips through getConfig", async () => {
    const mod = await loadConfigModule();
    const config = {
      deploymentUrl: "http://saved-host:9000",
      assistantId: "saved-assistant",
      langsmithApiKey: "sk-saved",
      mainAgentModel: "model-big",
      subagentModel: "model-small",
    };

    mod.saveConfig(config);

    expect(window.localStorage.getItem(CONFIG_KEY)).toBe(JSON.stringify(config));
    expect(mod.getConfig()).toEqual(config);
  });
});
