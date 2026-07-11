import { authClient } from "./client";
import { isAuthEnabled } from "./enabled";

/**
 * Bearer-token plumbing for backend requests in multi-user mode.
 *
 * Better Auth's JWT plugin mints short-lived (15 min) tokens from the session
 * cookie via /api/auth/token; we cache one until shortly before expiry and
 * refresh on demand. In zero-login mode everything here is a no-op.
 */

const EXPIRY_SLACK_MS = 30_000;

let cached: { token: string; expiresAtMs: number } | null = null;
let inflight: Promise<string | null> | null = null;

function decodeExpiryMs(token: string): number {
  try {
    const payload = token.split(".")[1];
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    if (typeof claims.exp === "number") return claims.exp * 1000;
  } catch {
    // Fall through to a conservative default below.
  }
  return Date.now() + 60_000;
}

/** Current bearer token, refreshed when missing/near expiry; null when auth is off or signed out. */
export async function getBearerToken(): Promise<string | null> {
  if (!isAuthEnabled()) return null;
  if (cached && Date.now() < cached.expiresAtMs - EXPIRY_SLACK_MS) return cached.token;
  inflight ??= (async () => {
    try {
      const { data, error } = await authClient.token();
      if (error || !data?.token) return null;
      cached = { token: data.token, expiresAtMs: decodeExpiryMs(data.token) };
      return data.token;
    } catch {
      return null;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** Drop the cached token (e.g. after a 401 or sign-out) so the next call refetches. */
export function clearBearerToken(): void {
  cached = null;
}

/**
 * SDK `onRequest` hook: injects `Authorization: Bearer …` into every
 * langgraph-sdk request (REST + SSE streams share this path).
 */
export async function authOnRequest(_url: URL, init: RequestInit): Promise<RequestInit> {
  const token = await getBearerToken();
  if (!token) return init;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers };
}

/**
 * fetch wrapper for the raw `/files/*` calls: injects the bearer and retries
 * once with a fresh token on 401 (expired-token race).
 */
export async function authedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = await getBearerToken();
  // Zero-login mode: pass the request through untouched (preserving arity).
  if (!token) return init === undefined ? fetch(input) : fetch(input, init);

  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status !== 401) return res;

  clearBearerToken();
  const fresh = await getBearerToken();
  if (!fresh || fresh === token) return res;
  headers.set("Authorization", `Bearer ${fresh}`);
  return fetch(input, { ...init, headers });
}
