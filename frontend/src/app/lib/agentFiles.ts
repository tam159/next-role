import type { Client } from "@langchain/langgraph-sdk";
import { AGENT_FILE_SOURCES, type AgentFileSources } from "@/app/config/agentFiles";

export type AgentFileSource = "state" | "store" | "disk";

export type AgentFile = {
  path: string;
  content: string;
  encoding: "utf-8" | "base64";
  source: AgentFileSource;
  /** Original key/path on the source, used for write-back. */
  sourceKey: string;
  /** Last-modified timestamp (epoch ms). Undefined for state files
   * (the graph state has no per-key timestamp); callers may overlay a
   * client-side stamp for those. */
  modifiedAt?: number;
};

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"]);
const BINARY_EXTS = new Set([
  ...IMAGE_EXTS,
  "pdf",
  "doc",
  "docx",
  "zip",
  "gz",
  "tar",
  "mp3",
  "mp4",
  "wav",
  "ogg",
  "woff",
  "woff2",
  "ttf",
  "otf",
]);

export function isImagePath(p: string): boolean {
  const ext = p.split(".").pop()?.toLowerCase() ?? "";
  return IMAGE_EXTS.has(ext);
}

export function isBinaryPath(p: string): boolean {
  const ext = p.split(".").pop()?.toLowerCase() ?? "";
  return BINARY_EXTS.has(ext);
}

export function getAgentFileSources(
  graphId: string | null | undefined
): AgentFileSources | undefined {
  if (!graphId) return undefined;
  return AGENT_FILE_SOURCES[graphId];
}

function stateFilesToAgentFiles(stateFiles: Record<string, unknown>): AgentFile[] {
  const out: AgentFile[] = [];
  for (const [key, raw] of Object.entries(stateFiles ?? {})) {
    const content = (() => {
      if (typeof raw === "string") {
        return raw;
      }
      if (
        typeof raw === "object" &&
        raw !== null &&
        "content" in (raw as Record<string, unknown>)
      ) {
        const inner = (raw as { content: unknown }).content;
        if (Array.isArray(inner)) return inner.join("\n");
        return String(inner ?? "");
      }
      return String(raw ?? "");
    })();
    out.push({
      path: key,
      content,
      encoding: "utf-8",
      source: "state",
      sourceKey: key,
    });
  }
  return out;
}

/**
 * Map a store-relative path (e.g. "/processed/foo.md") to the backend
 * `(namespace, key)` pair the langgraph store actually uses.
 *
 * The agent's `CompositeBackend` routes each `pathPrefixes` entry to its own
 * `StoreBackend` namespace by appending the prefix's path segments to the
 * agent's namespace prefix. We mirror that derivation here so list/read/write
 * from the frontend hit the same rows the agent wrote.
 *
 * Returns `null` when no configured prefix matches.
 */
export function resolveStoreLocation(
  cfg: NonNullable<AgentFileSources["store"]>,
  storeRel: string
): { namespace: string[]; key: string } | null {
  // Longest match first so e.g. "/interview_prep/" wins over a hypothetical "/interview/".
  const sorted = [...cfg.pathPrefixes].sort((a, b) => b.length - a.length);
  for (const prefix of sorted) {
    const trimmed = prefix.replace(/\/+$/, "");
    const matches = storeRel === trimmed || storeRel.startsWith(`${trimmed}/`);
    if (!matches) continue;
    const segments = trimmed.split("/").filter(Boolean);
    const namespace = [...cfg.namespacePrefix, ...segments];
    const remainder = storeRel.slice(trimmed.length);
    const key = remainder.startsWith("/") ? remainder : `/${remainder}`;
    return { namespace, key };
  }
  return null;
}

async function fetchStoreFiles(
  client: Client,
  cfg: NonNullable<AgentFileSources["store"]>
): Promise<AgentFile[]> {
  const out: AgentFile[] = [];
  await Promise.all(
    cfg.pathPrefixes.map(async (prefix) => {
      const trimmed = prefix.replace(/\/+$/, "");
      const segments = trimmed.split("/").filter(Boolean);
      const namespace = [...cfg.namespacePrefix, ...segments];
      try {
        const res = await client.store.searchItems(namespace, { limit: 200 });
        for (const item of res.items ?? []) {
          const value = item.value as Record<string, unknown>;
          const rawContent = value?.content;
          let content = "";
          if (typeof rawContent === "string") content = rawContent;
          else if (Array.isArray(rawContent)) content = rawContent.join("\n");
          else if (rawContent != null) content = String(rawContent);
          const encoding = value?.encoding === "base64" ? "base64" : "utf-8";
          const itemKey = item.key.startsWith("/") ? item.key : `/${item.key}`;
          const storeRel = `${trimmed}${itemKey}`;
          const updated =
            (item as { updatedAt?: string; updated_at?: string }).updatedAt ??
            (item as { updated_at?: string }).updated_at;
          const modifiedAt = updated ? Date.parse(updated) : undefined;
          out.push({
            path: storeRel,
            content,
            encoding: encoding as "utf-8" | "base64",
            source: "store",
            sourceKey: storeRel,
            modifiedAt: Number.isFinite(modifiedAt) ? modifiedAt : undefined,
          });
        }
      } catch (e) {
        console.warn(`store fetch failed for namespace ${namespace.join("/")}`, e);
      }
    })
  );
  return out;
}

async function fetchDiskFiles(cfg: NonNullable<AgentFileSources["disk"]>): Promise<AgentFile[]> {
  const params = new URLSearchParams({
    root: cfg.root,
    dirs: cfg.includeDirs.join(","),
  });
  const listRes = await fetch(`/api/files/list?${params}`);
  if (!listRes.ok) {
    console.warn("disk list failed", listRes.status, await listRes.text());
    return [];
  }
  const listData = (await listRes.json()) as {
    files: { path: string; isBinary: boolean; modifiedAt?: string }[];
  };

  const reads = await Promise.all(
    listData.files.map(async (f): Promise<AgentFile | null> => {
      try {
        const r = await fetch(`/api/files/read?path=${encodeURIComponent(f.path)}`);
        if (!r.ok) return null;
        const data = (await r.json()) as {
          content: string;
          encoding: "utf-8" | "base64";
        };
        const rootStripped = f.path.replace(new RegExp(`^/${cfg.root.replace(/\//g, "\\/")}`), "");
        const modifiedAt = f.modifiedAt ? Date.parse(f.modifiedAt) : undefined;
        return {
          path: rootStripped || f.path,
          content: data.content,
          encoding: data.encoding,
          source: "disk",
          sourceKey: f.path,
          modifiedAt: Number.isFinite(modifiedAt) ? modifiedAt : undefined,
        };
      } catch (e) {
        console.warn("disk read failed", f.path, e);
        return null;
      }
    })
  );
  return reads.filter((x): x is AgentFile => x !== null);
}

export async function fetchAgentFiles(args: {
  client: Client;
  graphId: string | null | undefined;
  stateFiles: Record<string, unknown>;
}): Promise<AgentFile[]> {
  const { client, graphId, stateFiles } = args;
  const cfg = getAgentFileSources(graphId);
  const stateList = stateFilesToAgentFiles(stateFiles);

  const tasks: Promise<AgentFile[]>[] = [];
  if (cfg?.store) {
    tasks.push(
      fetchStoreFiles(client, cfg.store).catch((e) => {
        console.warn("store fetch failed", e);
        return [];
      })
    );
  }
  if (cfg?.disk) {
    tasks.push(
      fetchDiskFiles(cfg.disk).catch((e) => {
        console.warn("disk fetch failed", e);
        return [];
      })
    );
  }
  const extra = (await Promise.all(tasks)).flat();

  const byPath = new Map<string, AgentFile>();
  for (const f of [...extra, ...stateList]) {
    byPath.set(f.path, f);
  }
  // Sort newest-first; files with no timestamp fall back to alphabetical
  // ordering at the bottom. The hook layer is expected to overlay a
  // client-side stamp on state-source files so they get a real position.
  return Array.from(byPath.values()).sort((a, b) => {
    const am = a.modifiedAt ?? -Infinity;
    const bm = b.modifiedAt ?? -Infinity;
    if (am !== bm) return bm - am;
    return a.path.localeCompare(b.path);
  });
}

export async function writeAgentFile(args: {
  client: Client;
  threadId: string | null;
  graphId: string | null | undefined;
  file: AgentFile;
  // For state-source writes we need the full files map.
  stateFiles?: Record<string, string>;
}): Promise<void> {
  const { client, threadId, graphId, file, stateFiles } = args;
  const cfg = getAgentFileSources(graphId);

  if (file.source === "store") {
    if (!cfg?.store) {
      throw new Error("Store backend not configured for this agent");
    }
    const loc = resolveStoreLocation(cfg.store, file.sourceKey);
    if (!loc) {
      throw new Error(
        `No matching store pathPrefix for ${file.sourceKey} (configured: ${cfg.store.pathPrefixes.join(", ")})`
      );
    }
    await client.store.putItem(loc.namespace, loc.key, {
      content: file.content,
      encoding: file.encoding,
    });
    return;
  }

  if (file.source === "disk") {
    const res = await fetch("/api/files/write", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: file.sourceKey,
        content: file.content,
        encoding: file.encoding,
      }),
    });
    if (!res.ok) {
      throw new Error(`Disk write failed: ${res.status} ${await res.text()}`);
    }
    return;
  }

  // state
  if (!threadId) throw new Error("No threadId for state write");
  const next = { ...(stateFiles ?? {}), [file.sourceKey]: file.content };
  await client.threads.updateState(threadId, { values: { files: next } });
}
