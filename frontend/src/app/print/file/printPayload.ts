export type PrintKind = "markdown" | "code" | "docx";

export type PrintPayload = {
  path: string;
  content: string;
  kind: PrintKind;
  language?: string;
};

export const PRINT_FILE_STORAGE_KEY = "nextrole:print-file";

export function parsePayload(raw: string | null): PrintPayload | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PrintPayload>;
    if (
      typeof parsed?.path === "string" &&
      typeof parsed?.content === "string" &&
      (parsed.kind === "markdown" || parsed.kind === "code" || parsed.kind === "docx")
    ) {
      return parsed as PrintPayload;
    }
    return null;
  } catch {
    return null;
  }
}

export function basenameWithoutExtension(path: string): string {
  const base = path.split("/").pop() || path;
  return base.replace(/\.[^.]+$/, "");
}
