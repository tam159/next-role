#!/usr/bin/env node
// Guard: the app and @langchain/react must resolve the SAME copy of
// @langchain/langgraph-sdk. The SDK is a *regular* dependency of
// @langchain/react (exact-pinned upstream), so a version conflict does NOT
// warn at install time — pnpm silently installs two copies, and `Client`
// instances created from one copy break instance identity inside the
// other's StreamController (the original parallel-subagent freeze era bug).
// pnpm only warns for *peer* conflicts (e.g. @langchain/core).
//
// Runs in pre-commit (Frontend LangChain SDK sync) and `pnpm quality`.
import { createRequire } from "node:module";
import { realpathSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const version = (pkgJsonPath) => JSON.parse(readFileSync(pkgJsonPath, "utf8")).version;

const appRequire = createRequire(path.join(frontendDir, "package.json"));
const appSdk = realpathSync(appRequire.resolve("@langchain/langgraph-sdk/package.json"));

// Resolve the SDK copy @langchain/react itself sees (its .pnpm sibling).
const reactDir = realpathSync(path.dirname(appRequire.resolve("@langchain/react/package.json")));
const reactRequire = createRequire(path.join(reactDir, "package.json"));
const reactSdk = realpathSync(reactRequire.resolve("@langchain/langgraph-sdk/package.json"));

if (appSdk !== reactSdk) {
  const wanted = version(reactSdk);
  console.error(
    `✗ Two copies of @langchain/langgraph-sdk are installed:\n` +
      `    app resolves        ${version(appSdk)}  (${appSdk})\n` +
      `    @langchain/react uses ${wanted}  (${reactSdk})\n` +
      `  Fix: pnpm --dir frontend add @langchain/langgraph-sdk@${wanted} --save-exact\n` +
      `  (then docker compose restart frontend)`
  );
  process.exit(1);
}
console.log(
  `✓ @langchain/langgraph-sdk ${version(appSdk)}: single copy, shared with @langchain/react`
);
