/// <reference types="./global.d.ts" />

import type { Hono } from "hono";
import { serve } from "@hono/node-server";
import * as path from "node:path";
import * as url from "node:url";
import { createLogger, format, transports } from "winston";
import { gracefulExit } from "exit-hook";
import { z } from "zod/v3";

const logger = createLogger({
  level: "debug",
  format: format.combine(
    format.errors({ stack: true }),
    format.timestamp(),
    format.json(),
    format.printf((info) => {
      const { timestamp, level, message, ...rest } = info;

      let event;
      if (typeof message === "string") {
        event = message;
      } else {
        event = JSON.stringify(message);
      }

      if (rest.stack) {
        rest.message = event;
        event = rest.stack;
      }

      return JSON.stringify({ timestamp, level, event, ...rest });
    }),
  ),
  transports: [
    new transports.Console({
      handleExceptions: true,
      handleRejections: true,
    }),
  ],
});

const HTTP_PORT = 5557;

const wrapHonoApp = (app: Hono) => {
  // We do this to avoid importing Hono from server dependencies
  // b/c the user's Hono version might be different than ours.
  // See warning here: https://hono.dev/docs/guides/middleware#built-in-middleware
  const newApp = new (Object.getPrototypeOf(app).constructor)() as Hono<{
    Variables: {
      "langgraph:body": string | ArrayBuffer | ReadableStream | null;
    };
  }>;

  // This endpoint is used to check if we can yield the routing to the Python server early.
  // Note: will always yield if user added a custom middleware, as in that case
  // the router will always find a suitable handler.
  newApp.options("/__langgraph_check", (c) => {
    const method = c.req.header("x-langgraph-method");
    const path = c.req.header("x-langgraph-path");
    if (!method || !path) return c.body(null, 400);

    const [handlers] = app.router.match(method, path);
    if (handlers.length === 0) return c.body(null, 404);
    return c.body(null, 200);
  });

  newApp.route("/", app);

  // `notFound` handler is overriden here to yield back to the Python server
  // alongside any accumulated headers from middlewares.
  // TODO: figure out how to compose the user-land `notFound` handler.
  newApp.notFound(async (c) => {
    // Send the request body back to the Python server
    // Use the cached body in-case the user mutated the body
    let payload: string | ArrayBuffer | ReadableStream | null =
      c.get("langgraph:body");

    if (payload == null) {
      try {
        payload = JSON.stringify(await c.req.json()) ?? null;
      } catch {
        // pass
      }
    }

    return c.body(payload, {
      status: 404,
      // This header is set to denote user-land 404s vs internal 404s.
      headers: {
        "x-langgraph-status": "not-found",
        "x-langgraph-body": payload != null ? "true" : "false",
      },
    });
  });

  return newApp;
};

async function registerHttp(appPath: string, options: { cwd: string }) {
  const [userFile, exportSymbol] = appPath.split(":", 2);
  const sourceFile = path.resolve(options.cwd, userFile);

  const user = (await import(url.pathToFileURL(sourceFile).toString()).then(
    (module) => module[exportSymbol || "default"],
  )) as Hono | undefined;

  if (!user) throw new Error(`Failed to load HTTP app: ${appPath}`);
  return wrapHonoApp(user);
}

async function main() {
  const mainStart = performance.now();
  logger.info("Starting HTTP loop", { pid: process.pid });

  const http = z
    .object({
      app: z.string().optional(),
      disable_assistants: z.boolean().default(false),
      disable_threads: z.boolean().default(false),
      disable_runs: z.boolean().default(false),
      disable_store: z.boolean().default(false),
      disable_meta: z.boolean().default(false),
      cors: z
        .object({
          allow_origins: z.array(z.string()).optional(),
          allow_methods: z.array(z.string()).optional(),
          allow_headers: z.array(z.string()).optional(),
          allow_credentials: z.boolean().optional(),
          allow_origin_regex: z.string().optional(),
          expose_headers: z.array(z.string()).optional(),
          max_age: z.number().optional(),
        })
        .optional(),
    })
    .parse(JSON.parse(process.env.LANGGRAPH_HTTP ?? "{}"));

  if (!http.app) throw new Error("No HTTP app path provided");

  // register loopback
  const urlSmb = Symbol.for("langgraph_api:url");
  const global = globalThis as unknown as { [urlSmb]?: string };
  global[urlSmb] = `http://localhost:${process.env.PORT || 9123}`;

  const registerStart = performance.now();
  const app = await registerHttp(http.app, { cwd: process.cwd() });
  const registerMs = Math.round(performance.now() - registerStart);
  const registerMeta: Record<string, unknown> = {
    app_path: http.app,
    duration_ms: registerMs,
  };

  if (registerMs > 30_000) {
    registerMeta.threshold_ms = 30_000;
    logger.error(
      "HTTP app registration exceeded error threshold. " +
        "Slow initialization can delay readiness and cause deployments to be marked unhealthy.",
      registerMeta,
    );
  } else if (registerMs > 3_000) {
    registerMeta.threshold_ms = 3_000;
    logger.warn(
      "HTTP app registration exceeded warn threshold. " +
        "Consider optimizing app initialization to improve startup time.",
      registerMeta,
    );
  } else {
    logger.info("HTTP app registered", registerMeta);
  }

  const totalStartupMs = Math.round(performance.now() - mainStart);
  serve({ fetch: app.fetch, hostname: "localhost", port: HTTP_PORT }, (c) =>
    logger.info("JS HTTP server listening", {
      address: c.address,
      port: c.port,
      total_startup_ms: totalStartupMs,
    }),
  );
}

process.on("uncaughtExceptionMonitor", (error) => {
  logger.error(error);
  gracefulExit();
});

main();
