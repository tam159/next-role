/// <reference types="./global.d.ts" />
import "./src/preload.mjs";

import { z } from "zod/v3";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { type GraphSchema, resolveGraph } from "./src/graph.mts";
import { build } from "@langchain/langgraph-ui";
import { checkLangGraphSemver } from "@langchain/langgraph-api/semver";
import {
  getStaticGraphSchema,
  GraphSpec,
} from "@langchain/langgraph-api/schema";
import { filterValidExportPath } from "./src/utils/files.mts";

const __dirname = new URL(".", import.meta.url).pathname;

async function main() {
  const specs = Object.entries(
    z
      .record(
        z.union([
          z.string(),
          z.object({ path: z.string(), description: z.string().nullish() }),
        ]),
      )
      .parse(JSON.parse(process.env.LANGSERVE_GRAPHS)),
  ).filter(([_, spec]) => filterValidExportPath(spec));

  let GRAPH_SCHEMAS: Record<string, Record<string, GraphSchema> | false> = {};

  const semver = await checkLangGraphSemver();
  const invalidPackages = semver.filter(
    (s) => !s.satisfies && s.version !== "0.0.0",
  );
  if (invalidPackages.length > 0) {
    console.error(
      `Some LangGraph.js dependencies required by the LangGraph API server are not up to date. \n` +
        `Please make sure to upgrade them to the required version:\n` +
        invalidPackages
          .map(
            (i) =>
              `- ${i.name}@${i.version} is not up to date. Required: ${i.required}`,
          )
          .join("\n") +
        "\n" +
        "Visit https://langchain-ai.github.io/langgraphjs/cloud/deployment/setup_javascript/ for more information.",
    );

    process.exit(1);
  }

  let failed = false;
  try {
    const resolveSpecs = Object.fromEntries<GraphSpec>(
      await Promise.all(
        specs.map(async ([graphId, rawSpec]) => {
          console.info(`[${graphId}]: Checking for source file existence`);
          const importPath =
            typeof rawSpec === "string" ? rawSpec : rawSpec.path;
          const { resolved, ...spec } = await resolveGraph(importPath, {
            onlyFilePresence: true,
          });

          return [graphId, spec] as [string, GraphSpec];
        }),
      ),
    );

    try {
      console.info("Extracting schemas");
      GRAPH_SCHEMAS = await getStaticGraphSchema(resolveSpecs, {
        timeoutMs: 120_000,
      });
    } catch (error) {
      console.error(`Error extracting schema: ${error}`);
    }

    await fs.writeFile(
      path.resolve(__dirname, "client.schemas.json"),
      JSON.stringify(
        Object.fromEntries(
          specs.map(([graphId]) => {
            const valid = GRAPH_SCHEMAS[graphId];
            if (valid == null || Object.values(valid).every((x) => x == null)) {
              return [graphId, false];
            }

            return [graphId, valid];
          }),
        ),
      ),
      { encoding: "utf-8" },
    );
  } catch (error) {
    console.error(`Error resolving graphs: ${error}`);
    failed = true;
  }

  // Build Gen UI assets
  try {
    console.info("Checking for UI assets");
    await fs.mkdir(path.resolve(__dirname, "ui"), { recursive: true });

    await build({ output: path.resolve(__dirname, "ui") });
  } catch (error) {
    console.error(`Error building UI: ${error}`);
    failed = true;
  }

  if (failed) process.exit(1);
}

main();
