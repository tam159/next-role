import { jwtClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";

/**
 * Better Auth browser client (same-origin /api/auth). `authClient.token()`
 * (from jwtClient) mints the short-lived JWT that backend requests carry as
 * `Authorization: Bearer …` in multi-user mode.
 */
export const authClient = createAuthClient({
  plugins: [jwtClient()],
});
