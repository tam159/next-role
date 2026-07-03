import { overrideFetchImplementation } from "langsmith";

const RUNS_RE = /^https:\/\/api\.smith\.langchain\.com\/.*runs(\/|$)/i;

export function patchFetch() {
  const shouldBlock =
    typeof process !== "undefined" &&
    !!(process.env && process.env.LANGSMITH_DISABLE_SAAS_RUNS === "true");

  if (shouldBlock) {
    overrideFetchImplementation(
      async (input: RequestInfo, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(input, init);

        if (req.method.toUpperCase() === "POST" && RUNS_RE.test(req.url)) {
          throw new Error(
            `Policy-blocked POST to ${new URL(req.url).pathname} — run tracking disabled`,
          );
        }

        return fetch(req);
      },
    );
  }
}
