/**
 * Bearer-token plumbing. token.ts holds module-level cache state, so each
 * test re-imports it fresh (resetModules) after stubbing the env.
 */

const tokenMock = vi.fn();

vi.mock("@/lib/auth/client", () => ({
  authClient: { token: (...args: unknown[]) => tokenMock(...args) },
}));

function makeJwt(expEpochSecs: number): string {
  const b64 = (o: object) =>
    Buffer.from(JSON.stringify(o)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "EdDSA" })}.${b64({ sub: "u1", exp: expEpochSecs })}.sig`;
}

async function loadModule() {
  vi.resetModules();
  return import("./token");
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("getBearerToken", () => {
  it("returns null without calling the API in zero-login mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    const { getBearerToken } = await loadModule();
    expect(await getBearerToken()).toBeNull();
    expect(tokenMock).not.toHaveBeenCalled();
  });

  it("fetches once and serves later calls from the cache", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    const jwt = makeJwt(Math.floor(Date.now() / 1000) + 900);
    tokenMock.mockResolvedValue({ data: { token: jwt }, error: null });
    const { getBearerToken } = await loadModule();

    expect(await getBearerToken()).toBe(jwt);
    expect(await getBearerToken()).toBe(jwt);
    expect(tokenMock).toHaveBeenCalledTimes(1);
  });

  it("refetches once the cached token nears expiry", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    const stale = makeJwt(Math.floor(Date.now() / 1000) + 5); // inside the 30s slack
    const fresh = makeJwt(Math.floor(Date.now() / 1000) + 900);
    tokenMock
      .mockResolvedValueOnce({ data: { token: stale }, error: null })
      .mockResolvedValueOnce({ data: { token: fresh }, error: null });
    const { getBearerToken } = await loadModule();

    expect(await getBearerToken()).toBe(stale);
    expect(await getBearerToken()).toBe(fresh);
    expect(tokenMock).toHaveBeenCalledTimes(2);
  });

  it("returns null when the session is gone", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    tokenMock.mockResolvedValue({ data: null, error: { status: 401 } });
    const { getBearerToken } = await loadModule();
    expect(await getBearerToken()).toBeNull();
  });
});

describe("authOnRequest", () => {
  it("adds the Authorization header to the request init", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    const jwt = makeJwt(Math.floor(Date.now() / 1000) + 900);
    tokenMock.mockResolvedValue({ data: { token: jwt }, error: null });
    const { authOnRequest } = await loadModule();

    const out = await authOnRequest(new URL("http://x/threads"), { headers: { a: "1" } });
    const headers = new Headers(out.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${jwt}`);
    expect(headers.get("a")).toBe("1");
  });

  it("leaves the init untouched in zero-login mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    const { authOnRequest } = await loadModule();
    const init = { headers: { a: "1" } };
    expect(await authOnRequest(new URL("http://x/threads"), init)).toBe(init);
  });
});

describe("authedFetch", () => {
  it("retries once with a fresh token on 401", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    const first = makeJwt(Math.floor(Date.now() / 1000) + 900);
    const second = makeJwt(Math.floor(Date.now() / 1000) + 1800);
    tokenMock
      .mockResolvedValueOnce({ data: { token: first }, error: null })
      .mockResolvedValueOnce({ data: { token: second }, error: null });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("expired", { status: 401 }))
      .mockResolvedValueOnce(new Response("ok", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const { authedFetch } = await loadModule();
    const res = await authedFetch("http://x/files/list");

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = new Headers(fetchMock.mock.calls[1][1].headers);
    expect(retryHeaders.get("Authorization")).toBe(`Bearer ${second}`);
  });

  it("does not retry non-401 failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    tokenMock.mockResolvedValue({
      data: { token: makeJwt(Math.floor(Date.now() / 1000) + 900) },
      error: null,
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response("boom", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    const { authedFetch } = await loadModule();
    const res = await authedFetch("http://x/files/list");
    expect(res.status).toBe(500);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
