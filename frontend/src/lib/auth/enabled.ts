/**
 * Multi-user auth is opt-in: unset/false keeps the zero-login single-user
 * mode (everything scoped to the "default" user), true requires login.
 *
 * Functions (not consts) so tests can `vi.stubEnv` without module resets;
 * Next.js still inlines the `NEXT_PUBLIC_*` reads at build time.
 */
export function isAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";
}

/** Whether the "Continue with Google" button is offered on the login page. */
export function isGoogleAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_GOOGLE_ENABLED === "true";
}

/**
 * Whether email + password sign-in/sign-up is offered (default on). Shared
 * deployments set `NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED=false` for
 * Google-only login; the Better Auth server reads the same flag, so the
 * endpoints are disabled too, not just hidden.
 */
export function isEmailPasswordAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED !== "false";
}
