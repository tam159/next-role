import * as nodeModule from "node:module";
const { register } = nodeModule;
import { pathToFileURL } from "node:url";
import { join } from "node:path";

// we only care about the payload, which contains the server definition
const graphs = JSON.parse(process.env.LANGSERVE_GRAPHS || "{}");
const cwd = process.cwd();

// find the first file, as `parentURL` needs to be a valid file URL
// if no graph found, just assume a dummy default file, which should
// be working fine as well.
const firstGraphFile =
  Object.values(graphs)
    .map((i) => {
      if (typeof i === "string") {
        return i.split(":").at(0);
      } else if (i && typeof i === "object" && i.path) {
        return i.path.split(":").at(0);
      }
      return null;
    })
    .filter(Boolean)
    .at(0) || "index.mts";

const graphParentURL = pathToFileURL(join(cwd, firstGraphFile)).toString();

// Enforce API @langchain/langgraph resolution.
//
// Node.js 24.15.0 (nodejs/node#61769, commit a03b5d39b8) moved the ESM
// loader and its full resolve/load plumbing into the V8 startup snapshot.
// Pre-24.15 the loader was lazy-initialized on first ESM import, so the
// off-thread worker spawned by module.register() had time to wire its
// hooks in before the first `--import` dispatch. With the loader already
// running from snapshot, the main thread's import chain can start before
// the worker is ready, causing the process to silently exit with code 0
// when the hook chain can't complete. The same release also marks
// module.register() as Type: Documentation-only deprecated (DEP0205) in
// favor of module.registerHooks(); the deprecation itself doesn't change
// runtime behavior, but it confirms the migration direction.
//
// Use module.registerHooks() (synchronous, main-thread, in-process hooks)
// when available (Node.js 22.15+ / 24.x) so we're on the same channel as
// tsx 4.21.1+ and avoid the worker-thread race entirely. Fall back to the
// legacy module.register() path for older Node.js versions.
if (typeof nodeModule.registerHooks === "function") {
  const OVERRIDE_RESOLVE = [
    // Override `@langchain/langgraph` or `@langchain/langgraph/prebuilt`,
    // but not `@langchain/langgraph-sdk`
    new RegExp(`^@langchain\\/langgraph(\\/.+)?$`),
    new RegExp(`^@langchain\\/langgraph-checkpoint(\\/.+)?$`),
  ];

  let langgraphPackageURL;

  nodeModule.registerHooks({
    resolve(specifier, context, nextResolve) {
      // HACK: @tailwindcss/node internally uses an ESM loader cache, which
      // does not play nicely with `tsx`. Node.js crashes with
      // "TypeError [ERR_INVALID_URL_SCHEME]: The URL must be of scheme file".
      // As it already is a valid URI, short-circuit the resolution.
      if (
        specifier.includes("@tailwindcss/node/dist/esm-cache.loader") &&
        specifier.startsWith("file://")
      ) {
        return {
          shortCircuit: true,
          url: specifier.replace(".mts", ".mjs"),
          format: "module",
        };
      }

      if (specifier === "@langchain/langgraph-checkpoint") {
        // resolve relative to @langchain/langgraph package instead
        if (!langgraphPackageURL) {
          const main = nextResolve("@langchain/langgraph", {
            ...context,
            parentURL: graphParentURL,
          });
          langgraphPackageURL = main.url.toString();
        }
        return nextResolve(specifier, {
          ...context,
          parentURL: langgraphPackageURL,
        });
      }

      if (OVERRIDE_RESOLVE.some((regex) => regex.test(specifier))) {
        const resolved = nextResolve(specifier, {
          ...context,
          parentURL: graphParentURL,
        });

        // If @langchain/langgraph is resolved first, cache it!
        if (specifier === "@langchain/langgraph" && !langgraphPackageURL) {
          langgraphPackageURL = resolved.url.toString();
        }
        return resolved;
      }
      return nextResolve(specifier, context);
    },
  });
} else {
  register("./load.hooks.mjs", import.meta.url, {
    parentURL: "data:",
    data: { parentURL: graphParentURL },
  });
}
