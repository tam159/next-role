import { BaseMessage } from "@langchain/core/messages";
import { parsePartialJson } from "@langchain/core/output_parsers";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function extractStringFromMessageContent(message: BaseMessage): string {
  // Core's `.text` accessor joins string content and `{type: "text"}` blocks.
  return message.text;
}

export function extractSubAgentContent(data: unknown): string {
  if (typeof data === "string") {
    return data;
  }

  if (data && typeof data === "object") {
    const dataObj = data as Record<string, unknown>;

    // Try to extract description first
    if (dataObj.description && typeof dataObj.description === "string") {
      return dataObj.description;
    }

    // Then try prompt
    if (dataObj.prompt && typeof dataObj.prompt === "string") {
      return dataObj.prompt;
    }

    // For output objects, try result
    if (dataObj.result && typeof dataObj.result === "string") {
      return dataObj.result;
    }

    // Fallback to JSON stringification
    return JSON.stringify(data, null, 2);
  }

  // Fallback for any other type
  return JSON.stringify(data, null, 2);
}

/**
 * Unwrap a ToolMessage-shaped wire envelope to its content. The v2 `tools`
 * channel's `tool-finished` event carries the full serialized ToolMessage
 * (`content` + `additional_kwargs`/`response_metadata`/`tool_call_id`/...).
 * The SDK strips it for `AssembledToolCall.output`, but
 * `SubagentDiscoverySnapshot.output` is the raw payload — without this,
 * the whole envelope JSON leaks into the subagent Output panel.
 */
export function unwrapToolPayload(value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    if (v.type === "tool" && "content" in v) {
      const content = v.content;
      if (typeof content === "string") return content;
      if (Array.isArray(content)) {
        return content
          .map((block) =>
            typeof block === "string"
              ? block
              : block && typeof block === "object" && "text" in block
                ? String((block as { text?: unknown }).text ?? "")
                : ""
          )
          .join("");
      }
      return content;
    }
  }
  return value;
}

export function toResultString(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Best-effort object from a streaming tool_call_chunk args string. */
export function parsePartialArgs(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  const parsed = parsePartialJson(raw);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}

/** Compact elapsed time between two instants: "<1s", "42s", "3m 07s". */
export function formatDuration(startedAt: Date, completedAt: Date): string {
  const totalSeconds = Math.round((completedAt.getTime() - startedAt.getTime()) / 1000);
  if (totalSeconds < 1) return "<1s";
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}
