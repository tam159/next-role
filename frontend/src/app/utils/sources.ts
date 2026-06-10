import { type BaseMessage, isAIMessage, isToolMessage } from "@langchain/core/messages";
import type { AssembledToolCall } from "@langchain/langgraph-sdk/stream";
import type { Source } from "@/app/types/types";
import { extractStringFromMessageContent } from "@/app/utils/utils";

const SEARCH_TOOL_NAMES = new Set(["tavily_search", "web_search"]);

const MARKDOWN_BLOCK = /^##\s+(.+?)\s*\n\*\*URL:\*\*\s+(\S+)/gm;

type ToolCallRef = { name: string };

function parseFromString(raw: string): Array<{ title: string; url: string }> {
  const trimmed = raw.trim();
  if (!trimmed) return [];

  // web_search returns a stringified dict from TavilyClient.search().
  // Python str(dict) and json.dumps both yield strings starting with `{`.
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    const parsed = tryParsePythonish(trimmed);
    if (parsed) {
      const fromJson = extractFromJsonShape(parsed);
      if (fromJson.length > 0) return fromJson;
    }
  }

  const out: Array<{ title: string; url: string }> = [];
  MARKDOWN_BLOCK.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = MARKDOWN_BLOCK.exec(raw)) !== null) {
    out.push({ title: match[1].trim(), url: match[2].trim() });
  }
  if (out.length > 0) return out;

  // Last resort: any URL preceded by **URL:**.
  const URL_ONLY = /\*\*URL:\*\*\s+(\S+)/g;
  while ((match = URL_ONLY.exec(raw)) !== null) {
    out.push({ title: match[1].trim(), url: match[1].trim() });
  }
  return out;
}

function tryParsePythonish(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    // fall through
  }
  try {
    // Python dict → JSON: single quotes, True/False/None.
    const json = raw
      .replace(/'/g, '"')
      .replace(/\bTrue\b/g, "true")
      .replace(/\bFalse\b/g, "false")
      .replace(/\bNone\b/g, "null");
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function extractFromJsonShape(data: unknown): Array<{ title: string; url: string }> {
  if (!data || typeof data !== "object") return [];
  const obj = data as Record<string, unknown>;
  const results = Array.isArray(obj.results) ? obj.results : null;
  if (!results) return [];
  const out: Array<{ title: string; url: string }> = [];
  for (const item of results) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    const url = typeof r.url === "string" ? r.url : "";
    const title = typeof r.title === "string" ? r.title : url;
    if (url) out.push({ title: title || url, url });
  }
  return out;
}

export function extractSources(messages: BaseMessage[]): Source[] {
  const searchToolCalls = new Map<string, ToolCallRef>();

  for (const message of messages) {
    if (!isAIMessage(message)) continue;
    for (const call of message.tool_calls ?? []) {
      if (!call.id || !call.name) continue;
      if (SEARCH_TOOL_NAMES.has(call.name)) {
        searchToolCalls.set(call.id, { name: call.name });
      }
    }
  }

  const seen = new Set<string>();
  const sources: Source[] = [];

  for (const message of messages) {
    if (!isToolMessage(message)) continue;
    const id = message.tool_call_id;
    if (!id || !searchToolCalls.has(id)) continue;
    const raw = extractStringFromMessageContent(message);
    const parsed = parseFromString(raw);
    parsed.forEach((entry, idx) => {
      if (seen.has(entry.url)) return;
      seen.add(entry.url);
      sources.push({
        id: `${id}:${idx}`,
        title: entry.title,
        url: entry.url,
        toolCallId: id,
      });
    });
  }

  return sources;
}

/**
 * Sources from a scoped tool-call projection (e.g. a subagent's calls via
 * `useToolCalls(stream, snapshot)`), where outputs arrive already parsed.
 */
export function extractSourcesFromToolCalls(toolCalls: AssembledToolCall[]): Source[] {
  const seen = new Set<string>();
  const sources: Source[] = [];

  for (const tc of toolCalls) {
    if (!SEARCH_TOOL_NAMES.has(tc.name) || tc.status !== "finished") continue;
    const parsed =
      typeof tc.output === "string" ? parseFromString(tc.output) : extractFromJsonShape(tc.output);
    parsed.forEach((entry, idx) => {
      if (seen.has(entry.url)) return;
      seen.add(entry.url);
      sources.push({
        id: `${tc.id}:${idx}`,
        title: entry.title,
        url: entry.url,
        toolCallId: tc.id,
      });
    });
  }

  return sources;
}
