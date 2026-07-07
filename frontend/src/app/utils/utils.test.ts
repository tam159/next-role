import { AIMessage } from "@langchain/core/messages";
import {
  cn,
  extractStringFromMessageContent,
  extractSubAgentContent,
  formatDuration,
  parsePartialArgs,
  toResultString,
  unwrapToolPayload,
} from "./utils";

describe("cn", () => {
  it("merges conflicting tailwind classes, last one wins", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("p-2 p-4")).toBe("p-4");
  });

  it("handles clsx conditionals and drops falsy values", () => {
    expect(cn("base", { active: true, hidden: false }, undefined, null, "extra")).toBe(
      "base active extra"
    );
  });
});

describe("extractStringFromMessageContent", () => {
  it("returns plain string content as-is", () => {
    expect(extractStringFromMessageContent(new AIMessage("hello world"))).toBe("hello world");
  });

  it("joins text content blocks", () => {
    const message = new AIMessage({
      content: [
        { type: "text", text: "Hello, " },
        { type: "text", text: "world" },
      ],
    });
    expect(extractStringFromMessageContent(message)).toBe("Hello, world");
  });
});

describe("extractSubAgentContent", () => {
  it("returns string input unchanged", () => {
    expect(extractSubAgentContent("already a string")).toBe("already a string");
  });

  it("prefers description over prompt and result", () => {
    expect(extractSubAgentContent({ description: "d", prompt: "p", result: "r" })).toBe("d");
  });

  it("falls back to prompt when description is missing or unusable", () => {
    expect(extractSubAgentContent({ prompt: "p", result: "r" })).toBe("p");
    // Empty-string and non-string descriptions are skipped.
    expect(extractSubAgentContent({ description: "", prompt: "p" })).toBe("p");
    expect(extractSubAgentContent({ description: 42, prompt: "p" })).toBe("p");
  });

  it("falls back to result when description and prompt are missing", () => {
    expect(extractSubAgentContent({ result: "r" })).toBe("r");
  });

  it("stringifies objects without any known field", () => {
    expect(extractSubAgentContent({ other: 1 })).toBe(JSON.stringify({ other: 1 }, null, 2));
  });

  it("stringifies non-string primitives", () => {
    expect(extractSubAgentContent(42)).toBe("42");
    expect(extractSubAgentContent(true)).toBe("true");
    expect(extractSubAgentContent(null)).toBe("null");
  });

  it("returns undefined for undefined input (JSON.stringify(undefined) is undefined)", () => {
    // Actual behavior: the declared `string` return type notwithstanding.
    expect(extractSubAgentContent(undefined)).toBeUndefined();
  });
});

describe("unwrapToolPayload", () => {
  it("unwraps a tool envelope with string content", () => {
    expect(unwrapToolPayload({ type: "tool", content: "hello" })).toBe("hello");
  });

  it("joins an array of text blocks (strings and {text} objects)", () => {
    const payload = {
      type: "tool",
      content: [{ type: "text", text: "a" }, "b", { type: "text", text: "c" }],
    };
    expect(unwrapToolPayload(payload)).toBe("abc");
  });

  it("maps blocks without usable text to empty strings", () => {
    const payload = { type: "tool", content: [{ foo: 1 }, { text: null }, 42] };
    expect(unwrapToolPayload(payload)).toBe("");
  });

  it("returns non-string, non-array envelope content as-is", () => {
    const content = { nested: true };
    expect(unwrapToolPayload({ type: "tool", content })).toBe(content);
  });

  it("passes through objects that are not tool envelopes", () => {
    const notEnvelope = { foo: 1 };
    expect(unwrapToolPayload(notEnvelope)).toBe(notEnvelope);
    const wrongType = { type: "other", content: "x" };
    expect(unwrapToolPayload(wrongType)).toBe(wrongType);
    const noContent = { type: "tool" };
    expect(unwrapToolPayload(noContent)).toBe(noContent);
  });

  it("leaves arrays untouched", () => {
    const arr = [{ type: "tool", content: "x" }];
    expect(unwrapToolPayload(arr)).toBe(arr);
  });

  it("passes through primitives, null, and undefined", () => {
    expect(unwrapToolPayload("plain")).toBe("plain");
    expect(unwrapToolPayload(null)).toBeNull();
    expect(unwrapToolPayload(undefined)).toBeUndefined();
  });
});

describe("toResultString", () => {
  it("returns undefined for null and undefined", () => {
    expect(toResultString(undefined)).toBeUndefined();
    expect(toResultString(null)).toBeUndefined();
  });

  it("returns strings unchanged, including the empty string", () => {
    expect(toResultString("hello")).toBe("hello");
    expect(toResultString("")).toBe("");
  });

  it("pretty-prints objects as JSON", () => {
    expect(toResultString({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(toResultString(42)).toBe("42");
  });

  it("falls back to String() for circular objects", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(toResultString(circular)).toBe("[object Object]");
  });
});

describe("parsePartialArgs", () => {
  it("parses complete JSON objects", () => {
    expect(parsePartialArgs('{"a": 1, "b": "two"}')).toEqual({ a: 1, b: "two" });
  });

  it("best-effort parses a truncated streaming JSON prefix", () => {
    expect(parsePartialArgs('{"query": "hel')).toEqual({ query: "hel" });
  });

  it("returns {} when the parse result is not a plain object", () => {
    expect(parsePartialArgs('"just a string"')).toEqual({});
    expect(parsePartialArgs("[1, 2")).toEqual({});
    expect(parsePartialArgs("not json at all")).toEqual({});
  });

  it("returns {} for undefined or empty input", () => {
    expect(parsePartialArgs(undefined)).toEqual({});
    expect(parsePartialArgs("")).toEqual({});
  });
});

describe("formatDuration", () => {
  const at = (iso: string) => new Date(iso);

  it("renders sub-second durations as <1s", () => {
    expect(formatDuration(at("2026-07-01T10:00:00.000Z"), at("2026-07-01T10:00:00.400Z"))).toBe(
      "<1s"
    );
  });

  it("renders seconds-only durations", () => {
    expect(formatDuration(at("2026-07-01T10:00:00Z"), at("2026-07-01T10:00:42Z"))).toBe("42s");
  });

  it("renders minutes with zero-padded seconds", () => {
    expect(formatDuration(at("2026-07-01T10:00:00Z"), at("2026-07-01T10:03:07Z"))).toBe("3m 07s");
  });
});
