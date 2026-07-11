import { betterAuth } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { jwt } from "better-auth/plugins";
import { Pool } from "pg";

/**
 * Better Auth server instance — the identity provider for multi-user mode.
 *
 * Server-only. Evaluated lazily: the /api/auth route dynamic-imports this
 * module only when auth is enabled, so zero-login mode never constructs the
 * DB pool or needs BETTER_AUTH_SECRET. The Better Auth CLI also loads it for
 * its one-time schema migration (see .env.example).
 *
 * The jwt plugin adds:
 *   GET /api/auth/token — mints a short-lived JWT from the session cookie
 *   GET /api/auth/jwks  — public keys the backend verifies bearers against
 * Identity contract: the JWT `sub` claim is the Better Auth user id — the
 * backend uses it as the owner/scope for threads, files, and memory.
 */

const googleClientId = process.env.GOOGLE_AUTH_CLIENT_ID;
const googleClientSecret = process.env.GOOGLE_AUTH_CLIENT_SECRET;

export const auth = betterAuth({
  baseURL: process.env.BETTER_AUTH_URL,
  database: new Pool({ connectionString: process.env.AUTH_DATABASE_URL }),
  emailAndPassword: {
    enabled: true,
  },
  ...(googleClientId && googleClientSecret
    ? {
        socialProviders: {
          google: { clientId: googleClientId, clientSecret: googleClientSecret },
        },
      }
    : {}),
  // nextCookies must stay the last plugin (it wraps the others' cookie writes).
  plugins: [jwt(), nextCookies()],
});
