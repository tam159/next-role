import { AIMessage, type BaseMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { AssembledToolCall } from "@langchain/react";
import { extractSources, extractSourcesFromToolCalls } from "./sources";

/** AI message that issues a single tool call with the given id/name. */
function aiToolCall(id: string, name = "tavily_search"): AIMessage {
  return new AIMessage({ content: "", tool_calls: [{ id, name, args: {} }] });
}

function toolResult(id: string, content: string): ToolMessage {
  return new ToolMessage({ content, tool_call_id: id });
}

describe("extractSources", () => {
  it("returns [] when there are no messages", () => {
    expect(extractSources([])).toEqual([]);
  });

  it("extracts sources from JSON tool output with a results array", () => {
    const output = JSON.stringify({
      results: [
        { title: "One", url: "https://one.example" },
        { title: "Two", url: "https://two.example" },
      ],
    });
    const messages: BaseMessage[] = [
      new HumanMessage("find stuff"),
      aiToolCall("call1"),
      toolResult("call1", output),
    ];
    expect(extractSources(messages)).toEqual([
      { id: "call1:0", title: "One", url: "https://one.example", toolCallId: "call1" },
      { id: "call1:1", title: "Two", url: "https://two.example", toolCallId: "call1" },
    ]);
  });

  it("parses a Pythonish dict output via quote/True/False/None coercion", () => {
    const output =
      "{'results': [{'title': 'X', 'url': 'https://x.example'}], 'ok': True, " +
      "'flag': False, 'none': None}";
    const messages: BaseMessage[] = [
      aiToolCall("call1", "web_search"),
      toolResult("call1", output),
    ];
    expect(extractSources(messages)).toEqual([
      { id: "call1:0", title: "X", url: "https://x.example", toolCallId: "call1" },
    ]);
  });

  it("falls back to url as title when a JSON result has no title", () => {
    const output = JSON.stringify({
      results: [{ url: "https://untitled.example" }, { title: "no url here" }],
    });
    const messages: BaseMessage[] = [aiToolCall("c"), toolResult("c", output)];
    expect(extractSources(messages)).toEqual([
      {
        id: "c:0",
        title: "https://untitled.example",
        url: "https://untitled.example",
        toolCallId: "c",
      },
    ]);
  });

  it("parses markdown output with ## Title + **URL:** blocks", () => {
    const output = [
      "## First Result",
      "**URL:** https://one.example",
      "",
      "Some summary text.",
      "",
      "## Second Result",
      "**URL:** https://two.example",
    ].join("\n");
    const messages: BaseMessage[] = [aiToolCall("call1"), toolResult("call1", output)];
    expect(extractSources(messages)).toEqual([
      { id: "call1:0", title: "First Result", url: "https://one.example", toolCallId: "call1" },
      { id: "call1:1", title: "Second Result", url: "https://two.example", toolCallId: "call1" },
    ]);
  });

  it("uses the url as the title in the **URL:**-only fallback", () => {
    const output = "Top hit below\n**URL:** https://only.example\nno headings anywhere";
    const messages: BaseMessage[] = [aiToolCall("call1"), toolResult("call1", output)];
    expect(extractSources(messages)).toEqual([
      {
        id: "call1:0",
        title: "https://only.example",
        url: "https://only.example",
        toolCallId: "call1",
      },
    ]);
  });

  it("dedupes the same URL across multiple tool messages", () => {
    const output = JSON.stringify({ results: [{ title: "Dup", url: "https://dup.example" }] });
    const messages: BaseMessage[] = [
      aiToolCall("c1"),
      aiToolCall("c2"),
      toolResult("c1", output),
      toolResult("c2", output),
    ];
    const sources = extractSources(messages);
    expect(sources).toHaveLength(1);
    expect(sources[0]).toEqual({
      id: "c1:0",
      title: "Dup",
      url: "https://dup.example",
      toolCallId: "c1",
    });
  });

  it("ignores tool results from non-search tools", () => {
    const output = JSON.stringify({ results: [{ title: "T", url: "https://t.example" }] });
    const messages: BaseMessage[] = [aiToolCall("c1", "write_file"), toolResult("c1", output)];
    expect(extractSources(messages)).toEqual([]);
  });

  it("ignores a ToolMessage whose tool_call_id has no matching AI tool call", () => {
    const output = JSON.stringify({ results: [{ title: "T", url: "https://t.example" }] });
    const messages: BaseMessage[] = [aiToolCall("real"), toolResult("orphan", output)];
    expect(extractSources(messages)).toEqual([]);
  });

  it("returns [] for unparseable search output", () => {
    const messages: BaseMessage[] = [aiToolCall("c1"), toolResult("c1", "no links in here")];
    expect(extractSources(messages)).toEqual([]);
  });
});

describe("extractSourcesFromToolCalls", () => {
  function assembled(over: {
    id: string;
    name: string;
    status: "running" | "finished" | "error";
    output: unknown;
  }): AssembledToolCall {
    return {
      name: over.name,
      callId: over.id,
      id: over.id,
      namespace: [],
      input: {},
      args: {},
      output: over.output,
      status: over.status,
      error: undefined,
    } as AssembledToolCall;
  }

  const resultsJson = { results: [{ title: "Hit", url: "https://hit.example" }] };

  it("parses a JSON string output from a finished search call", () => {
    const calls = [
      assembled({
        id: "tc1",
        name: "tavily_search",
        status: "finished",
        output: JSON.stringify(resultsJson),
      }),
    ];
    expect(extractSourcesFromToolCalls(calls)).toEqual([
      { id: "tc1:0", title: "Hit", url: "https://hit.example", toolCallId: "tc1" },
    ]);
  });

  it("reads an already-parsed object output directly", () => {
    const calls = [
      assembled({ id: "tc2", name: "web_search", status: "finished", output: resultsJson }),
    ];
    expect(extractSourcesFromToolCalls(calls)).toEqual([
      { id: "tc2:0", title: "Hit", url: "https://hit.example", toolCallId: "tc2" },
    ]);
  });

  it("skips calls that are not finished", () => {
    const calls = [
      assembled({ id: "run", name: "tavily_search", status: "running", output: resultsJson }),
      assembled({ id: "err", name: "tavily_search", status: "error", output: resultsJson }),
    ];
    expect(extractSourcesFromToolCalls(calls)).toEqual([]);
  });

  it("skips finished calls from non-search tools", () => {
    const calls = [
      assembled({ id: "tc3", name: "read_file", status: "finished", output: resultsJson }),
    ];
    expect(extractSourcesFromToolCalls(calls)).toEqual([]);
  });

  it("dedupes URLs across calls, keeping the first occurrence", () => {
    const calls = [
      assembled({ id: "a", name: "tavily_search", status: "finished", output: resultsJson }),
      assembled({ id: "b", name: "web_search", status: "finished", output: resultsJson }),
    ];
    const sources = extractSourcesFromToolCalls(calls);
    expect(sources).toHaveLength(1);
    expect(sources[0].toolCallId).toBe("a");
  });
});
