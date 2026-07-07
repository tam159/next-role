export function parseToolError(result: unknown): { code?: string; message?: string } | null {
  if (result == null) return null;

  // Cap inspection at 256 chars — error markers always appear at the start, and
  // a long streaming payload here would force a JSON.stringify of the full body.
  const raw =
    typeof result === "string"
      ? result
      : (() => {
          try {
            return JSON.stringify(result);
          } catch {
            return "";
          }
        })();
  if (!raw) return null;
  const text = raw.length > 256 ? raw.slice(0, 256) : raw;
  const trimmed = text.trim();
  const startsWithError = /^(error|exception|failed)\b\s*:?\s*/i.test(trimmed);
  if (!startsWithError) return null;

  const objectStart = trimmed.indexOf("{");
  if (objectStart !== -1) {
    try {
      const parsed = JSON.parse(trimmed.slice(objectStart).replace(/'/g, '"'));
      const error = parsed?.error ?? parsed;
      if (error?.code || error?.status || error?.message) {
        return {
          code: error.code ? String(error.code) : error.status,
          message: error.message ? String(error.message) : trimmed,
        };
      }
    } catch {
      // Fall through to regex detection for Python-style dicts or plain strings.
    }
  }

  const codeMatch =
    trimmed.match(/\bcode['"]?\s*:\s*(\d{3,})/i) ?? trimmed.match(/\b(\d{3})\s+[A-Z_]+\b/);
  const statusMatch = trimmed.match(/\bstatus['"]?\s*:\s*['"]?([A-Z_]+)/i);

  return {
    code: codeMatch?.[1] ?? statusMatch?.[1],
    message: trimmed,
  };
}

export function previewValue(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") {
    return value.length > 96 ? `${value.slice(0, 96)}...` : value;
  }
  // For objects/arrays we only need the first ~96 chars of the stringified form.
  // JSON.stringify on a large streaming payload here is the dominant cost during
  // streaming, so guard with try/catch and slice the result.
  try {
    const text = JSON.stringify(value);
    if (!text) return null;
    return text.length > 96 ? `${text.slice(0, 96)}...` : text;
  } catch {
    return null;
  }
}
