import { isAuthEnabled } from "@/lib/auth/enabled";

/**
 * Better Auth handler (/api/auth/*): sign-in/up/out, session, token, JWKS.
 *
 * In zero-login mode the route 404s without ever evaluating the auth server
 * module — no DB pool, no BETTER_AUTH_SECRET required. The handler pair is
 * built once and cached across requests.
 */

type NextHandlers = {
  GET: (req: Request) => Promise<Response>;
  POST: (req: Request) => Promise<Response>;
};

let handlersPromise: Promise<NextHandlers> | null = null;

function loadHandlers(): Promise<NextHandlers> {
  handlersPromise ??= (async () => {
    const [{ toNextJsHandler }, { auth }] = await Promise.all([
      import("better-auth/next-js"),
      import("@/lib/auth/server"),
    ]);
    return toNextJsHandler(auth);
  })();
  return handlersPromise;
}

const disabled = () => new Response("Not Found", { status: 404 });

export async function GET(req: Request): Promise<Response> {
  if (!isAuthEnabled()) return disabled();
  return (await loadHandlers()).GET(req);
}

export async function POST(req: Request): Promise<Response> {
  if (!isAuthEnabled()) return disabled();
  return (await loadHandlers()).POST(req);
}
