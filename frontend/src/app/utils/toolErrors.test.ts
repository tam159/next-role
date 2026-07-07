import { parseToolError, previewValue } from "@/app/utils/toolErrors";

describe("parseToolError", () => {
  it("returns null for null and undefined", () => {
    expect(parseToolError(null)).toBeNull();
    expect(parseToolError(undefined)).toBeNull();
  });

  it("returns null for benign strings without an error marker", () => {
    expect(parseToolError("All good")).toBeNull();
    expect(parseToolError("Wrote 3 files to the workspace")).toBeNull();
  });

  it("detects an Error: prefix and returns the message without a code", () => {
    const result = parseToolError("Error: something broke");
    expect(result).not.toBeNull();
    expect(result?.message).toBe("Error: something broke");
    expect(result?.code).toBeUndefined();
  });

  it("detects Exception and Failed prefixes", () => {
    expect(parseToolError("Exception: boom")?.message).toBe("Exception: boom");
    expect(parseToolError("Failed to reach the server")?.message).toBe(
      "Failed to reach the server"
    );
  });

  it("extracts code and message from an embedded JSON error object", () => {
    const result = parseToolError('Error: {"error": {"code": 429, "message": "Rate limited"}}');
    expect(result).toEqual({ code: "429", message: "Rate limited" });
  });

  it("extracts the code from a single-quoted python-style dict", () => {
    const raw = "Error: {'error': {'code': 429}}";
    const result = parseToolError(raw);
    expect(result?.code).toBe("429");
    // No message field in the dict — falls back to the full trimmed text.
    expect(result?.message).toBe(raw);
  });

  it("extracts a numeric code from free text", () => {
    const result = parseToolError("Error: request rejected with code: 429");
    expect(result?.code).toBe("429");
    expect(result?.message).toBe("Error: request rejected with code: 429");
  });

  it("extracts the code from a '429 TOO_MANY_REQUESTS' pattern", () => {
    expect(parseToolError("Error: 429 TOO_MANY_REQUESTS")?.code).toBe("429");
  });

  it("extracts an upper-case status token as the code", () => {
    const result = parseToolError("Error: status: 'RESOURCE_EXHAUSTED' for the model");
    expect(result?.code).toBe("RESOURCE_EXHAUSTED");
  });

  it("caps the inspected message at 256 characters", () => {
    const input = `Error: ${"x".repeat(500)}`;
    const result = parseToolError(input);
    expect(result?.message).toBe(input.slice(0, 256));
    expect(result?.message).toHaveLength(256);
  });

  it("stringifies non-string input before matching", () => {
    // JSON.stringify of an object starts with "{", which never matches the
    // Error/Exception/Failed start-of-text marker — so structured (non-string)
    // results are never reported as errors.
    expect(parseToolError({ error: { code: 429, message: "boom" } })).toBeNull();
    expect(parseToolError(429)).toBeNull();
  });

  it("returns null when the input cannot be stringified", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(parseToolError(circular)).toBeNull();
  });
});

describe("previewValue", () => {
  it("returns null for null and undefined", () => {
    expect(previewValue(null)).toBeNull();
    expect(previewValue(undefined)).toBeNull();
  });

  it("passes short strings through unchanged", () => {
    expect(previewValue("hello")).toBe("hello");
    expect(previewValue("a".repeat(96))).toBe("a".repeat(96));
  });

  it("truncates strings longer than 96 chars to 96 chars plus '...'", () => {
    const result = previewValue("a".repeat(97));
    expect(result).toBe(`${"a".repeat(96)}...`);
    expect(result).toHaveLength(99);
  });

  it("renders objects and arrays as compact JSON", () => {
    expect(previewValue({ a: 1, b: "two" })).toBe('{"a":1,"b":"two"}');
    expect(previewValue([1, 2, 3])).toBe("[1,2,3]");
  });

  it("truncates long stringified objects to 96 chars plus '...'", () => {
    const value = { text: "y".repeat(200) };
    expect(previewValue(value)).toBe(`${JSON.stringify(value).slice(0, 96)}...`);
  });

  it("returns null for circular objects", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(previewValue(circular)).toBeNull();
  });

  it("stringifies numbers and booleans", () => {
    expect(previewValue(42)).toBe("42");
    expect(previewValue(0)).toBe("0");
    expect(previewValue(true)).toBe("true");
    expect(previewValue(false)).toBe("false");
  });
});
