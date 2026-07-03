/// <reference types="./global.d.ts" />

import { z } from "zod/v3";
import { Context, Hono } from "hono";
import { serve } from "@hono/node-server";
import { zValidator } from "@hono/zod-validator";
import { streamSSE, stream } from "hono/streaming";
import { HTTPException } from "hono/http-exception";
import { fetch } from "undici";
import pRetry from "p-retry";
import {
  getConfig,
  BaseStore,
  Item,
  Operation,
  Command,
  Send,
  OperationResults,
  type Checkpoint,
  type CheckpointMetadata,
  type CheckpointTuple,
  type CompiledGraph,
} from "@langchain/langgraph";
import {
  BaseCheckpointSaver,
  type ChannelVersions,
  type ChannelProtocol,
} from "@langchain/langgraph-checkpoint";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { serialiseAsDict, serializeError } from "./src/utils/serde.mjs";
import * as importMap from "./src/utils/importMap.mjs";
import { buildExperimentReplicas } from "./src/utils/experiment-tracing.mjs";

import { createLogger, format, transports } from "winston";

import { load } from "@langchain/core/load";
import { BaseMessageChunk, isBaseMessage } from "@langchain/core/messages";
import type { PyItem, PyResult } from "./src/utils/pythonSchemas.mts";
import type { RunnableConfig } from "@langchain/core/runnables";
import {
  GraphSchema,
  resolveGraph,
  GraphSpec,
  type CompiledGraphFactory,
} from "./src/graph.mts";
import { asyncExitHook, gracefulExit } from "exit-hook";
import { awaitAllCallbacks } from "@langchain/core/callbacks/promises";
import { LangChainTracer } from "@langchain/core/tracers/tracer_langchain";
import type { BaseCallbackHandler } from "@langchain/core/callbacks/base";
import { StatusCode } from "hono/utils/http-status";
import {
  authenticate,
  authorize,
  registerAuth,
} from "@langchain/langgraph-api/auth";
import {
  getRuntimeGraphSchema,
  getStaticGraphSchema,
} from "@langchain/langgraph-api/schema";
import { filterValidExportPath } from "./src/utils/files.mts";
import { patchFetch } from "./traceblock.mts";
import { writeHeapSnapshot } from "node:v8";

const injectConfigFormatter = format((info) => {
  const config = getConfig();
  if (config == null) return info;

  const node = config.metadata?.["langgraph_node"];
  if (node != null) info.langgraph_node = node;

  return info;
});

const logger = createLogger({
  level: "debug",
  format: format.combine(
    injectConfigFormatter(),
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

const GLOBAL_LOGGER = Symbol.for("langgraph.api.sdk-logger");
type GLOBAL_LOGGER = typeof GLOBAL_LOGGER;

const maybeGlobal = globalThis as unknown as { [GLOBAL_LOGGER]: typeof logger };
maybeGlobal[GLOBAL_LOGGER] = logger;

let GRAPH_SCHEMA: Record<string, Record<string, GraphSchema> | false> = {};
let GRAPH_OPTIONS: {
  checkpointer?: BaseCheckpointSaver<string | number>;
  store?: BaseStore;
} = {};
let nodesExecuted = 0;
function incrementNodes() {
  nodesExecuted++;
  logger.debug(`Incremented nodes executed to ${nodesExecuted}`);
}

const version = await (async () => {
  try {
    const packageJson = await import("@langchain/langgraph/package.json");

    if (
      "default" in packageJson &&
      typeof packageJson.default === "object" &&
      packageJson.default != null
    ) {
      return packageJson.default.version || undefined;
    }

    if ("version" in packageJson) {
      return packageJson.version || undefined;
    }
  } catch (error) {
    logger.error(error);
  }
  return undefined;
})();

const GRAPH_RESOLVED: Record<
  string,
  CompiledGraph<string> | CompiledGraphFactory<string>
> = {};
const GRAPH_SPEC: Record<string, GraphSpec> = {};

async function getGraph(
  graphId: string,
  config: Parameters<CompiledGraphFactory<string>>[0],
  name: string | null | undefined,
) {
  if (!GRAPH_RESOLVED[graphId])
    throw new HTTPException(404, { message: `Graph "${graphId}" not found` });

  const factoryConfig = {
    ...config,
    checkpointer: GRAPH_OPTIONS.checkpointer,
    store: GRAPH_OPTIONS.store,
  };

  const resolved =
    typeof GRAPH_RESOLVED[graphId] === "function"
      ? await GRAPH_RESOLVED[graphId](factoryConfig)
      : GRAPH_RESOLVED[graphId];

  if (GRAPH_OPTIONS.checkpointer) {
    // @ts-expect-error BaseCheckpointSaver<string | number> != BaseCheckpointSaver<string>
    resolved.checkpointer = GRAPH_OPTIONS.checkpointer;
  }

  if (GRAPH_OPTIONS.store) {
    resolved.store = GRAPH_OPTIONS.store;
  }

  if (resolved.name == null || resolved.name === "LangGraph") {
    resolved.name = name ?? undefined;
  }

  return resolved;
}

async function getOrExtractSchema(graphId: string) {
  if (!(graphId in GRAPH_SPEC)) {
    throw new Error(`Spec for ${graphId} not found`);
  }

  if (!GRAPH_SCHEMA[graphId]) {
    // This is only set during build phase
    if (GRAPH_SCHEMA[graphId] === false) {
      throw new Error(`Failed to locate schema for "${graphId}"`);
    }

    try {
      const timer = logger.startTimer();

      let timeoutMs: number | undefined = undefined;
      try {
        timeoutMs = Number.parseInt(
          process.env.LANGGRAPH_SCHEMA_RESOLVE_TIMEOUT_MS || "30000",
          10,
        );
        if (Number.isNaN(timeoutMs) || timeoutMs <= 0) timeoutMs = undefined;
      } catch {
        // ignore
      }

      GRAPH_SCHEMA[graphId] = await getStaticGraphSchema(GRAPH_SPEC[graphId], {
        timeoutMs,
      });
      timer.done({ message: `Extracting schema for ${graphId} finished` });
    } catch (error) {
      throw new Error(`Failed to extract schema for "${graphId}": ${error}`);
    }
  }

  return GRAPH_SCHEMA[graphId];
}

const GRAPH_PORT = 5556;
const REMOTE_PORT = 5555;

const RunnableConfigSchema = z.object({
  tags: z.array(z.string()).optional(),
  metadata: z.record(z.unknown()).optional(),
  run_name: z.string().optional(),
  max_concurrency: z.number().optional(),
  recursion_limit: z.number().optional(),
  configurable: z.record(z.unknown()).optional(),
  run_id: z.string().uuid().optional(),
});

const getRunnableConfig = (
  userConfig: z.infer<typeof RunnableConfigSchema> | null | undefined,
) => {
  if (!userConfig) return {};
  return {
    configurable: userConfig.configurable,
    tags: userConfig.tags,
    metadata: userConfig.metadata,
    runName: userConfig.run_name,
    maxConcurrency: userConfig.max_concurrency,
    recursionLimit: userConfig.recursion_limit,
    runId: userConfig.run_id,
  };
};

function tryFetch(...args: Parameters<typeof fetch>) {
  return pRetry(
    async () => {
      const response = await fetch(...args).catch((error) => {
        throw new Error(`${args[0]} connecfailed: ${error}`);
      });

      if (!response.ok) {
        let errorMessage = `${args[0]} failed: HTTP ${response.status}`;
        try {
          errorMessage += `: ${await response.text()}`;
        } catch { }
        throw new Error(errorMessage);
      }

      return response;
    },
    {
      retries: 3,
      factor: 2,
      minTimeout: 1000,
      onFailedAttempt: (error) => void logger.error(error),
    },
  );
}

async function sendRecv<T = any>(
  method: `${"checkpointer" | "store"}_${string}`,
  data: unknown,
): Promise<T> {
  const res = await tryFetch(`http://localhost:${REMOTE_PORT}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  return (await load(await res.text(), {
    importMap,
    optionalImportEntrypoints: [],
    optionalImportsMap: {},
    secretsMap: {},
  })) as T;
}

const HEARTBEAT_MS = 5_000;
const handleInvoke = <T extends z.ZodType<any>>(
  name: string,
  _schema: T,
  handler: (rawPayload: z.infer<T>) => Promise<any>,
) => {
  return async (c: Context<any, any, { in: z.infer<T>; out: any }>) => {
    const graphId = c.req.param("graphId");
    const body = c.req.valid("json") as any;

    // send heartbeat every HEARTBEAT_INTERVAL_MS
    // to prevent connection from timing out
    c.header("Content-Type", "application/json");
    return stream(c, async (stream) => {
      let resolved: Promise<any> = Promise.resolve();
      const enqueueWrite = (payload: Uint8Array | string) => {
        resolved = stream.write(payload);
        return resolved;
      };

      // orjson.loads(...) does ignore the
      // whitespace prefix, so we can use that
      // as a heartbeat
      let interval = setInterval(() => enqueueWrite(" "), HEARTBEAT_MS);

      try {
        const response = JSON.stringify(
          await handler({ graph_id: graphId, ...body }),
        );

        clearInterval(interval);
        await enqueueWrite(response);
      } catch (error) {
        // Log the error since the stack trace won't be carried over to Python
        logger.error(error);
        // Return an error object that the Python side can handle
        const errorResponse = JSON.stringify({
          __error__: true,
          ...serializeError(error),
        });
        clearInterval(interval);
        await enqueueWrite(errorResponse);
      }
    });
  };
};

const handleStream = <T extends z.ZodType<any>>(
  name: string,
  _schema: T,
  handler: (rawPayload: z.infer<T>) => AsyncGenerator<any, void, unknown>,
) => {
  return (c: Context<any, any, { in: z.infer<T>; out: any }>) => {
    const graphId = c.req.param("graphId");
    const body = c.req.valid("json") as any;
    return streamSSE(c, async (stream) => {
      let resolve: Promise<any> = Promise.resolve();
      let timer: NodeJS.Timeout | undefined;
      const sendHeartbeat = () => {
        clearTimeout(timer);
        resolve = stream.writeln(": heartbeat");
        timer = setTimeout(sendHeartbeat, HEARTBEAT_MS);
        return resolve;
      };

      const sendSSE = (event: string, data: unknown) => {
        clearTimeout(timer);
        resolve = stream.writeSSE({ data: serialiseAsDict(data), event });
        timer = setTimeout(sendHeartbeat, HEARTBEAT_MS);
        return resolve;
      };

      try {
        timer = setTimeout(sendHeartbeat, HEARTBEAT_MS);
        for await (const data of handler({ graph_id: graphId, ...body })) {
          await sendSSE(name, data);
        }
      } catch (error) {
        // Still print out the error, as the stack
        // trace is not carried over in Python
        logger.error(error);
        await sendSSE("error", serializeError(error));
      }

      clearTimeout(timer);
      await resolve;
    });
  };
};

// Runtime-only callbacks must never cross the checkpoint boundary. The v3
// stream injects a ``StreamProtocolMessagesHandler`` into ``config.callbacks``;
// when that config is handed to the checkpointer it is sent to Python,
// echoed back on the returned tuple's ``config`` / ``parent_config``, and
// then fails ``load()`` because the handler serializes as a
// ``not_implemented`` object (breaking branch / regenerate). The checkpointer
// only needs ``configurable`` ids, so drop ``callbacks`` before sending.
function stripConfigCallbacks(
  config: RunnableConfig | undefined,
): RunnableConfig | undefined {
  if (config == null || typeof config !== "object" || !("callbacks" in config))
    return config;
  const { callbacks: _callbacks, ...rest } = config as RunnableConfig & {
    callbacks?: unknown;
  };
  return rest;
}

class RemoteCheckpointer extends BaseCheckpointSaver<number | string> {
  async getTuple(config: RunnableConfig): Promise<CheckpointTuple | undefined> {
    const result = await sendRecv("checkpointer_get_tuple", {
      config: stripConfigCallbacks(config),
    });

    if (!result) return undefined;
    return {
      checkpoint: result.checkpoint,
      config: result.config,
      metadata: result.metadata,
      parentConfig: result.parent_config,
      pendingWrites: result.pending_writes,
    };
  }

  async *list(
    config: RunnableConfig,
    options?: {
      limit?: number;
      before?: RunnableConfig;
      filter?: Record<string, any>;
    },
  ): AsyncGenerator<CheckpointTuple> {
    const result = await sendRecv("checkpointer_list", {
      config: stripConfigCallbacks(config),
      ...options,
      ...(options?.before
        ? { before: stripConfigCallbacks(options.before) }
        : {}),
    });

    for (const item of result) {
      yield {
        checkpoint: item.checkpoint,
        config: item.config,
        metadata: item.metadata,
        parentConfig: item.parent_config,
        pendingWrites: item.pending_writes,
      };
    }
  }

  async put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    newVersions: ChannelVersions,
  ): Promise<RunnableConfig> {
    return await sendRecv<RunnableConfig>("checkpointer_put", {
      config: stripConfigCallbacks(config),
      checkpoint,
      metadata,
      new_versions: newVersions,
    });
  }

  async putWrites(
    config: RunnableConfig,
    writes: [string, unknown][],
    taskId: string,
  ): Promise<void> {
    await sendRecv("checkpointer_put_writes", {
      config: stripConfigCallbacks(config),
      writes,
      taskId,
    });
  }

  getNextVersion(
    current: number | string | undefined,
    _channel: ChannelProtocol,
  ): string {
    let currentVersion = 0;

    if (current == null) {
      currentVersion = 0;
    } else if (typeof current === "number") {
      currentVersion = current;
    } else if (typeof current === "string") {
      currentVersion = Number.parseInt(current.split(".")[0], 10);
    }

    const nextVersion = String(currentVersion + 1).padStart(32, "0");
    const nextHash = Math.random().toFixed(16).substring(2).padEnd(16, "0");
    return `${nextVersion}.${nextHash}`;
  }
}

function camelToSnake(operation: Operation) {
  const snakeCaseKeys = (obj: Record<string, any>): Record<string, any> => {
    return Object.fromEntries(
      Object.entries(obj).map(([key, value]) => {
        const snakeKey = key.replace(
          /[A-Z]/g,
          (letter) => `_${letter.toLowerCase()}`,
        );
        if (
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value)
        ) {
          return [snakeKey, snakeCaseKeys(value)];
        }
        return [snakeKey, value];
      }),
    );
  };

  if ("namespace" in operation && "key" in operation) {
    return {
      namespace: operation.namespace,
      key: operation.key,
      ...("value" in operation ? { value: operation.value } : {}),
    };
  } else if ("namespacePrefix" in operation) {
    return {
      namespace_prefix: operation.namespacePrefix,
      filter: operation.filter,
      limit: operation.limit,
      offset: operation.offset,
    };
  } else if ("matchConditions" in operation) {
    return {
      match_conditions: operation.matchConditions?.map((condition) => ({
        match_type: condition.matchType,
        path: condition.path,
      })),
      max_depth: operation.maxDepth,
      limit: operation.limit,
      offset: operation.offset,
    };
  }

  return snakeCaseKeys(operation) as Operation;
}

function pyItemToJs(item?: PyItem): Item | undefined {
  if (!item) {
    return undefined;
  }
  return {
    namespace: item.namespace,
    key: item.key,
    value: item.value,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function isPyItem(result: unknown): result is PyItem {
  return (
    result != null &&
    typeof result === "object" &&
    "value" in result &&
    "key" in result
  );
}

export class RemoteStore extends BaseStore {
  async batch<Op extends Operation[]>(
    operations: Op,
  ): Promise<OperationResults<Op>> {
    const results = await sendRecv<PyResult[]>("store_batch", {
      operations: operations.map(camelToSnake),
    });

    return results.map((result) => {
      if (Array.isArray(result)) {
        return result.map((item) => (isPyItem(item) ? pyItemToJs(item) : item));
      }

      return isPyItem(result) ? pyItemToJs(result) : result;
    }) as OperationResults<Op>;
  }

  async get(namespace: string[], key: string): Promise<Item | null> {
    return await sendRecv<Item | null>("store_get", { namespace, key });
  }

  async search(
    namespacePrefix: string[],
    options?: {
      filter?: Record<string, any>;
      limit?: number;
      offset?: number;
    },
  ): Promise<Item[]> {
    return await sendRecv<Item[]>("store_search", {
      namespace_prefix: namespacePrefix,
      ...options,
    });
  }

  async put(
    namespace: string[],
    key: string,
    value: Record<string, any>,
  ): Promise<void> {
    await sendRecv("store_put", { namespace, key, value });
  }

  async delete(namespace: string[], key: string): Promise<void> {
    await sendRecv("store_delete", { namespace, key });
  }

  async listNamespaces(options: {
    prefix?: string[];
    suffix?: string[];
    maxDepth?: number;
    limit?: number;
    offset?: number;
  }): Promise<string[][]> {
    const data = await sendRecv<{ namespaces: string[][] }>(
      "store_list_namespaces",
      { max_depth: options?.maxDepth, ...options },
    );
    return data.namespaces;
  }
}

const StreamEventsPayload = z.object({
  graph_id: z.string(),
  graph_name: z.string().nullish(),
  graph_config: RunnableConfigSchema.nullish(),
  input: z.unknown(),
  command: z
    .object({
      resume: z.unknown().nullish(),
      goto: z.custom<Send | string | (Send | string)[]>().nullish(),
      graph: z.string().nullish(),
      update: z
        .union([
          z.record(z.unknown()),
          z.array(z.tuple([z.string(), z.unknown()])),
        ])
        .nullish(),
    })
    .nullish(),
  stream_mode: z
    .union([z.string(), z.array(z.string())])
    .optional(),
  config: RunnableConfigSchema.nullish(),
  interrupt_before: z.union([z.array(z.string()), z.literal("*")]).nullish(),
  interrupt_after: z.union([z.array(z.string()), z.literal("*")]).nullish(),
  subgraphs: z.boolean().optional(),
  // Selects the wire shape the worker expects back. ``"v3"`` is only sent
  // by the Protocol v2 event-streaming endpoints and switches this handler
  // onto LangGraphJS's native v3 stream (ProtocolEvents). Absent / ``"v2"``
  // keeps the legacy ``on_chain_stream`` behavior used by the older
  // streaming endpoints.
  stream_protocol: z.enum(["v2", "v3"]).nullish(),
});

function reviveCommand(
  command: z.infer<typeof StreamEventsPayload>["command"],
): Command | undefined {
  if (command == null) return undefined;
  let { goto, update, resume, graph } = command;

  goto ??= undefined;
  update ??= undefined;
  resume ??= undefined;
  graph ??= undefined;

  if (goto != null && !Array.isArray(goto)) goto = [goto];
  goto = goto?.map((item) => {
    if (typeof item === "string") return item;
    return new Send(item.node, item.args);
  });

  return new Command({ goto, update, resume, graph });
}

async function* streamEventsRequest(
  rawPayload: z.infer<typeof StreamEventsPayload>,
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const config = getRunnableConfig(payload.config);
  const graph = await getGraph(graphId, config, payload.graph_name);
  const input = reviveCommand(payload.command) ?? payload.input;
  // TODO Check if it's a remote graph and don't set in that case
  config.configurable = {
    ...config.configurable,
    ["__pregel_node_finished"]: incrementNodes,
  };

  const userStreamMode =
    payload.stream_mode == null
      ? []
      : Array.isArray(payload.stream_mode)
        ? payload.stream_mode
        : [payload.stream_mode];

  const graphStreamMode: Set<
    | "updates"
    | "debug"
    | "values"
    | "tasks"
    | "checkpoints"
    | "messages"
    | "custom"
  > = new Set();
  if (payload.stream_mode) {
    for (const mode of userStreamMode) {
      if (mode === "messages") {
        graphStreamMode.add("values");
      } else if (mode === "messages-tuple") {
        graphStreamMode.add("messages");
      } else {
        graphStreamMode.add(mode);
      }
    }
  }

  const messages: Record<string, BaseMessageChunk> = {};
  const completedIds = new Set<string>();

  let interruptBefore: typeof payload.interrupt_before =
    payload.interrupt_before ?? undefined;

  if (Array.isArray(interruptBefore) && interruptBefore.length === 0)
    interruptBefore = undefined;

  let interruptAfter: typeof payload.interrupt_after =
    payload.interrupt_after ?? undefined;

  if (Array.isArray(interruptAfter) && interruptAfter.length === 0)
    interruptAfter = undefined;

  const streamMode = [...graphStreamMode];

  if (version != null) {
    config.metadata ??= {};
    config.metadata.langgraph_version = version;
  }

  // Mirror the Python worker's experiment-routing setup for JS graphs. The
  // Python `tracing_context(replicas=...)` cannot cross the sidecar boundary,
  // so build replicas from the same reserved configurable keys and attach a
  // LangChainTracer carrying them. The framework skips its default tracer
  // when one with name "langchain_tracer" is already present in
  // config.callbacks (see @langchain/core callbacks/manager.js).
  const replicas = buildExperimentReplicas(config.configurable);
  const callbacks: BaseCallbackHandler[] | undefined = replicas
    ? [new LangChainTracer({ replicas })]
    : undefined;

  // Protocol v2 event-streaming runs request ``stream_protocol: "v3"``.
  // Drive the run through LangGraphJS's native v3 stream so its
  // StreamMux / transformer pipeline emits protocol-aligned ProtocolEvents
  // (messages content blocks, tools, custom:* channels, checkpoints)
  // directly. The worker forwards these straight into
  // ``EventStreamingSession`` — none of the legacy ``on_chain_stream``
  // reconstruction below runs. The graph's own ``streamTransformers`` are
  // merged in automatically by ``#streamEventsV3``, so custom channels work
  // without any per-channel patching here. Legacy endpoints fall through to
  // the unchanged ``version: "v2"`` path so their behavior is preserved.
  if (payload.stream_protocol === "v3") {
    // The v3 overload forces ``streamMode``/``subgraphs`` internally and
    // returns an awaitable ``GraphRunStream`` of ProtocolEvents. Cast away
    // the pinned ``CompiledGraph`` typing, which may predate the v3
    // overload depending on the installed ``@langchain/langgraph``.
    const protocolStream = await (
      graph as unknown as {
        streamEvents: (
          input: unknown,
          options: Record<string, unknown>,
        ) => Promise<AsyncIterable<unknown>>;
      }
    ).streamEvents(input, {
      ...config,
      version: "v3",
      interruptBefore,
      interruptAfter,
      ...(callbacks ? { callbacks } : {}),
    });

    for await (const event of protocolStream) {
      yield event;
    }
    return;
  }

  for await (const data of graph.streamEvents(input, {
    ...config,
    version: "v2",
    streamMode,
    subgraphs: payload.subgraphs,
    interruptBefore,
    interruptAfter,
    ...(callbacks ? { callbacks } : {}),
  })) {
    // TODO: upstream this fix to LangGraphJS
    if (streamMode.length === 1 && !Array.isArray(data.data.chunk)) {
      data.data.chunk = [streamMode[0], data.data.chunk];
    }

    if (payload.subgraphs) {
      if (Array.isArray(data.data.chunk) && data.data.chunk.length === 2) {
        data.data.chunk = [[], ...data.data.chunk];
      }
    }

    yield data;

    if (userStreamMode.includes("messages")) {
      if (data.event === "on_chain_stream" && data.run_id === config.runId) {
        const newMessages: Array<BaseMessageChunk> = [];
        const [_, chunk]: [string, any] = data.data.chunk;

        let chunkMessages: Array<BaseMessageChunk> = [];
        if (
          typeof chunk === "object" &&
          chunk != null &&
          "messages" in chunk &&
          !isBaseMessage(chunk)
        ) {
          chunkMessages = chunk?.messages;
        }

        if (!Array.isArray(chunkMessages)) {
          chunkMessages = [chunkMessages];
        }

        for (const message of chunkMessages) {
          if (!message.id || completedIds.has(message.id)) continue;
          completedIds.add(message.id);
          newMessages.push(message);
        }

        if (newMessages.length > 0) {
          yield {
            event: "on_custom_event",
            name: "messages/complete",
            data: newMessages,
          };
        }
      } else if (
        data.event === "on_chat_model_stream" &&
        !data.tags?.includes("nostream")
      ) {
        const message: BaseMessageChunk = data.data.chunk;

        if (!message.id) continue;

        if (messages[message.id] == null) {
          messages[message.id] = message;
          yield {
            event: "on_custom_event",
            name: "messages/metadata",
            data: { [message.id]: { metadata: data.metadata } },
          };
        } else {
          messages[message.id] = messages[message.id].concat(message);
        }

        yield {
          event: "on_custom_event",
          name: "messages/partial",
          data: [messages[message.id]],
        };
      }
    }
  }
}

const GetGraphPayload = z.object({
  graph_id: z.string(),
  graph_name: z.string().nullish(),
  graph_config: RunnableConfigSchema.nullish(),
  config: RunnableConfigSchema.nullish(),
  xray: z.union([z.number(), z.boolean()]).nullish(),
});

async function getGraphRequest(rawPayload: z.infer<typeof GetGraphPayload>) {
  const { graph_id: graphId, ...payload } = rawPayload;

  const graph = await getGraph(
    graphId,
    getRunnableConfig(payload.graph_config),
    payload.graph_name,
  );

  const drawable = await graph.getGraphAsync({
    ...getRunnableConfig(payload.config),
    xray: payload.xray ?? undefined,
  });
  return drawable.toJSON();
}

const GetSubgraphsPayload = z.object({
  graph_id: z.string(),
  graph_config: RunnableConfigSchema.nullish(),
  graph_name: z.string().nullish(),
  namespace: z.string().nullish(),
  recurse: z.boolean().nullish(),
});

async function getSubgraphsRequest(
  rawPayload: z.infer<typeof GetSubgraphsPayload>,
) {
  const { graph_id: graphId, ...payload } = rawPayload;

  const config = getRunnableConfig(payload.graph_config);
  const graph = await getGraph(graphId, config, payload.graph_name);

  const result: Array<[name: string, schema: Record<string, any>]> = [];
  let graphSchemaPromise: ReturnType<typeof getOrExtractSchema> | undefined;

  for await (const [ns, subgraph] of graph.getSubgraphsAsync(
    payload.namespace ?? undefined,
    payload.recurse ?? undefined,
  )) {
    const schema = await (async () => {
      const runtimeSchema = await getRuntimeGraphSchema(subgraph);
      if (runtimeSchema) return runtimeSchema;

      graphSchemaPromise ??= getOrExtractSchema(graphId);
      const graphSchema = await graphSchemaPromise;
      const rootGraphId = Object.keys(graphSchema).find(
        (i) => !i.includes("|"),
      );
      if (!rootGraphId) throw new Error("Failed to find root graph");
      return graphSchema[`${rootGraphId}|${ns}`] || graphSchema[rootGraphId];
    })();

    result.push([ns, schema]);
  }

  return Object.fromEntries(result);
}

const GetStatePayload = z.object({
  graph_id: z.string(),
  graph_config: RunnableConfigSchema.nullish(),
  graph_name: z.string().nullish(),
  config: RunnableConfigSchema,
  subgraphs: z.boolean().nullish(),
});

async function getStateRequest(rawPayload: z.infer<typeof GetStatePayload>) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graphConfig = getRunnableConfig(payload.graph_config);
  const graph = await getGraph(graphId, graphConfig, payload.graph_name);

  const state = await graph.getState(getRunnableConfig(payload.config), {
    subgraphs: payload.subgraphs ?? undefined,
  });

  // TODO: just send the JSON directly, don't ser/de twice
  return JSON.parse(serialiseAsDict(state));
}

const UpdateStatePayload = z.object({
  graph_id: z.string(),
  graph_config: RunnableConfigSchema.nullish(),
  graph_name: z.string().nullish(),
  config: RunnableConfigSchema,
  values: z.unknown(),
  as_node: z.string().nullish(),
});

async function updateStateRequest(
  rawPayload: z.infer<typeof UpdateStatePayload>,
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graphConfig = getRunnableConfig(payload.graph_config);
  const graph = await getGraph(graphId, graphConfig, payload.graph_name);

  const config = await graph.updateState(
    getRunnableConfig(payload.config),
    payload.values,
    payload.as_node ?? undefined,
  );

  return config;
}

const GetSchemaPayload = z.object({
  graph_id: z.string(),
  graph_config: RunnableConfigSchema.nullish(),
  graph_name: z.string().nullish(),
});

async function getSchemaRequest(payload: z.infer<typeof GetSchemaPayload>) {
  const { graph_id: graphId } = payload;

  const config = getRunnableConfig(payload.graph_config);
  const graph = await getGraph(graphId, config, payload.graph_name);

  const schema = await (async () => {
    const runtimeSchema = await getRuntimeGraphSchema(graph);
    if (runtimeSchema) return runtimeSchema;

    const graphSchema = await getOrExtractSchema(graphId);
    const rootGraphId = Object.keys(graphSchema).find((i) => !i.includes("|"));
    if (!rootGraphId) throw new Error("Failed to find root graph");
    return graphSchema[rootGraphId];
  })();

  return schema;
}

const GetStateHistoryPayload = z.object({
  graph_id: z.string(),
  graph_config: RunnableConfigSchema.nullish(),
  graph_name: z.string().nullish(),
  config: RunnableConfigSchema,
  limit: z.number().nullish(),
  before: RunnableConfigSchema.nullish(),
  filter: z.record(z.unknown()).nullish(),
});

async function* getStateHistoryRequest(
  rawPayload: z.infer<typeof GetStateHistoryPayload>,
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const config = getRunnableConfig(payload.graph_config);
  const graph = await getGraph(graphId, config, payload.graph_name);

  for await (const item of graph.getStateHistory(
    getRunnableConfig(payload.config),
    {
      limit: payload.limit ?? undefined,
      before: payload.before ? getRunnableConfig(payload.before) : undefined,
      filter: payload.filter ?? undefined,
    },
  )) {
    yield item;
  }
}

const __dirname = new URL(".", import.meta.url).pathname;

async function main() {
  const mainStart = performance.now();
  logger.info("Starting graph loop", { pid: process.pid });

  const app = new Hono();

  GRAPH_OPTIONS = {
    checkpointer: new RemoteCheckpointer(),
    store: new RemoteStore(),
  };

  const specs = Object.entries(
    z
      .record(
        z.union([
          z.string(),
          z.object({ path: z.string(), description: z.string().nullish() }),
        ]),
      )
      .parse(JSON.parse(process.env.LANGSERVE_GRAPHS ?? "{}")),
  ).filter(([_, spec]) => filterValidExportPath(spec));

  if (!process.argv.includes("--skip-schema-cache")) {
    try {
      GRAPH_SCHEMA = JSON.parse(
        await fs.readFile(path.resolve(__dirname, "client.schemas.json"), {
          encoding: "utf-8",
        }),
      );
    } catch {
      // pass
    }
  }

  const GRAPH_WARN_MS = 3_000;
  const GRAPH_ERROR_MS = 30_000;

  const graphTimings: { graphId: string; durationMs: number }[] = [];
  const resolveStart = performance.now();

  await Promise.all(
    specs.map(async ([graphId, rawSpec]) => {
      const start = performance.now();
      logger.info("Resolving graph", { graph_id: graphId });
      const importPath = typeof rawSpec === "string" ? rawSpec : rawSpec.path;
      const { resolved, ...spec } = await resolveGraph(importPath);

      GRAPH_RESOLVED[graphId] = resolved;
      GRAPH_SPEC[graphId] = spec;

      const durationMs = Math.round(performance.now() - start);
      graphTimings.push({ graphId, durationMs });

      const meta = {
        graph_id: graphId,
        duration_ms: durationMs,
        import_path: importPath,
        threshold_ms: 0,
      };

      if (durationMs > GRAPH_ERROR_MS) {
        meta.threshold_ms = GRAPH_ERROR_MS;
        logger.error(
          "Graph import exceeded error threshold. " +
          "Slow initialization (often due to work executed at import time) can delay readiness, " +
          "slow scale-out velocity, and may cause deployments to be marked unhealthy.",
          meta,
        );
      } else if (durationMs > GRAPH_WARN_MS) {
        meta.threshold_ms = GRAPH_WARN_MS;
        logger.warn(
          "Graph import exceeded warn threshold. " +
          "Slow initialization (often due to work executed at import time) can delay readiness, " +
          "slow scale-out velocity, and may cause deployments to be marked unhealthy.",
          meta,
        );
      } else {
        logger.info("Resolved graph", meta);
      }
    }),
  );

  const resolveTotal = Math.round(performance.now() - resolveStart);
  graphTimings.sort((a, b) => b.durationMs - a.durationMs);

  const totalMeta = {
    total_duration_ms: resolveTotal,
    graph_count: specs.length,
    timings: graphTimings,
    threshold_ms: 0,
  };

  if (resolveTotal > GRAPH_ERROR_MS) {
    totalMeta.threshold_ms = GRAPH_ERROR_MS;
    logger.error(
      "All graphs resolved but exceeded error threshold. " +
      "Consider reducing the number of graphs or optimizing their imports.",
      totalMeta,
    );
  } else if (resolveTotal > GRAPH_WARN_MS) {
    totalMeta.threshold_ms = GRAPH_WARN_MS;
    logger.warn(
      "All graphs resolved but exceeded warn threshold. " +
      "Consider optimizing graph imports to improve startup time.",
      totalMeta,
    );
  } else {
    logger.info("All graphs resolved", totalMeta);
  }

  app.post(
    "/:graphId/streamEvents",
    zValidator("json", StreamEventsPayload),
    handleStream("streamEvents", StreamEventsPayload, streamEventsRequest),
  );

  app.post(
    "/:graphId/getGraph",
    zValidator("json", GetGraphPayload),
    handleInvoke("getGraph", GetGraphPayload, getGraphRequest),
  );

  app.post(
    "/:graphId/getSubgraphs",
    zValidator("json", GetSubgraphsPayload),
    handleInvoke("getSubgraphs", GetSubgraphsPayload, getSubgraphsRequest),
  );

  app.post(
    "/:graphId/getState",
    zValidator("json", GetStatePayload),
    handleInvoke("getState", GetStatePayload, getStateRequest),
  );

  app.post(
    "/:graphId/updateState",
    zValidator("json", UpdateStatePayload),
    handleInvoke("updateState", UpdateStatePayload, updateStateRequest),
  );

  app.post(
    "/:graphId/getSchema",
    zValidator("json", GetSchemaPayload),
    handleInvoke("getSchema", GetSchemaPayload, getSchemaRequest),
  );

  app.post(
    "/:graphId/getStateHistory",
    zValidator("json", GetStateHistoryPayload),
    handleStream(
      "getStateHistory",
      GetStateHistoryPayload,
      getStateHistoryRequest,
    ),
  );

  app.post(
    "/:graphId/getNodesExecuted",
    zValidator("json", GetNodesExecutedPayload),
    handleInvoke(
      "getNodesExecuted",
      GetNodesExecutedPayload,
      getNodesExecutedRequest,
    ),
  );

  // Load LANGGRAPH_AUTH
  const auth = z
    .object({
      path: z.string().optional(),
      disable_studio_auth: z.boolean().optional(),
    })
    .parse(JSON.parse(process.env.LANGGRAPH_AUTH ?? "{}"));

  if (filterValidExportPath(auth.path)) {
    await registerAuth(auth, { cwd: process.cwd() });

    app.post("/auth/authenticate", async (c) => {
      try {
        const rawHeaders = c.req.raw.headers;
        const authUrl = rawHeaders.get("x-langgraph-auth-url") as string;
        const method = rawHeaders.get("x-langgraph-auth-method") as string;

        const headers = new Headers(rawHeaders);
        headers.delete("x-langgraph-auth-url");
        headers.delete("x-langgraph-auth-method");
        const context = await authenticate(
          new Request(authUrl, { headers, method }),
        );

        return c.json(context);
      } catch (error) {
        if (error instanceof HTTPException) {
          return c.json(
            {
              ...serializeError(error),
              status: error.res?.status ?? error.status,
              headers: error.res?.headers,
            },
            error.status as StatusCode,
          );
        }

        return c.json(serializeError(error), 403);
      }
    });

    app.post("/auth/authorize", async (c) => {
      try {
        return c.json(await authorize(await c.req.json()));
      } catch (error) {
        if (error instanceof HTTPException) {
          return c.json(serializeError(error), error.status);
        }

        return c.json(serializeError(error), 500);
      }
    });
  }

  app.get("/ok", (c) => c.json({ ok: true }));

  app.get("/debug/heapdump", async (c) => {
    try {
      const target = `/tmp/heapdump-${Date.now()}.heapsnapshot`;
      await fs.mkdir(path.dirname(target), { recursive: true });
      const written = writeHeapSnapshot(target);
      return c.json({ ok: true, written }); // 200
    } catch (error) {
      if (error instanceof HTTPException) {
        return c.json(serializeError(error), error.status);
      }
      return c.json(serializeError(error), 500);
    }
  });

  app.onError((err, c) => {
    logger.error(err);
    if (err instanceof HTTPException && err.status === 401) {
      return err.getResponse();
    }

    const { message } = serializeError(err);
    return c.text(message || "Internal server error", 500);
  });

  const totalStartupMs = Math.round(performance.now() - mainStart);
  serve({ fetch: app.fetch, hostname: "localhost", port: GRAPH_PORT }, (c) =>
    logger.info("JS graph server listening", {
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

const GetNodesExecutedPayload = z.object({
  graph_id: z.string(),
});

async function getNodesExecutedRequest(
  _payload: z.infer<typeof GetNodesExecutedPayload>,
) {
  const value = nodesExecuted;
  nodesExecuted = 0;
  logger.debug(
    `Returning ${value} nodes executed. Reset nodes executed to ${nodesExecuted}.`,
  );
  return { nodesExecuted: value };
}
patchFetch();
asyncExitHook(() => awaitAllCallbacks(), { wait: 3_000 });
main();
