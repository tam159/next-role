/**
 * Route-level gating of the Better Auth handler. The enabled case mocks
 * better-auth and the server config so no DB pool or secret is needed.
 */

const { toNextJsHandlerMock, getHandlerMock, postHandlerMock } = vi.hoisted(() => {
  const getHandlerMock = vi.fn(async () => new Response("get-ok"));
  const postHandlerMock = vi.fn(async () => new Response("post-ok"));
  return {
    getHandlerMock,
    postHandlerMock,
    toNextJsHandlerMock: vi.fn(() => ({ GET: getHandlerMock, POST: postHandlerMock })),
  };
});

vi.mock("better-auth/next-js", () => ({ toNextJsHandler: toNextJsHandlerMock }));
vi.mock("@/lib/auth/server", () => ({ auth: { handler: "stub" } }));

async function loadRoute() {
  vi.resetModules();
  return import("./route");
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("/api/auth/[...all] route", () => {
  it("404s without touching better-auth in zero-login mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    const route = await loadRoute();

    const res = await route.GET(new Request("http://localhost/api/auth/get-session"));
    expect(res.status).toBe(404);
    const postRes = await route.POST(new Request("http://localhost/api/auth/sign-in/email"));
    expect(postRes.status).toBe(404);
    expect(toNextJsHandlerMock).not.toHaveBeenCalled();
  });

  it("delegates to the Better Auth handler when enabled", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    const route = await loadRoute();

    const getRes = await route.GET(new Request("http://localhost/api/auth/get-session"));
    expect(await getRes.text()).toBe("get-ok");
    const postRes = await route.POST(new Request("http://localhost/api/auth/sign-in/email"));
    expect(await postRes.text()).toBe("post-ok");

    expect(getHandlerMock).toHaveBeenCalledTimes(1);
    expect(postHandlerMock).toHaveBeenCalledTimes(1);
    // Handler pair is built once and cached.
    expect(toNextJsHandlerMock).toHaveBeenCalledTimes(1);
  });
});
