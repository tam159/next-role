import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveSafe } from "../_lib";

const ALLOWED_EXTS = new Set(["pdf", "doc", "docx", "txt", "md"]);
const MAX_BYTES = 10 * 1024 * 1024;

type UploadResult = {
  uploaded: { path: string; size: number }[];
  errors: { name: string; reason: string }[];
};

function reject(name: string, reason: string, errors: UploadResult["errors"]): void {
  errors.push({ name, reason });
}

export async function POST(req: NextRequest) {
  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json({ error: "Expected multipart/form-data" }, { status: 400 });
  }

  const dirField = form.get("path");
  if (typeof dirField !== "string" || !dirField) {
    return NextResponse.json({ error: "Missing 'path' field" }, { status: 400 });
  }

  const fileEntries = form.getAll("file").filter((v): v is File => v instanceof File);
  if (fileEntries.length === 0) {
    return NextResponse.json({ error: "No files provided" }, { status: 400 });
  }

  const result: UploadResult = { uploaded: [], errors: [] };

  for (const file of fileEntries) {
    const name = file.name;
    const ext = name.split(".").pop()?.toLowerCase() ?? "";
    if (!ALLOWED_EXTS.has(ext)) {
      reject(name, `Unsupported extension: .${ext}`, result.errors);
      continue;
    }
    if (file.size > MAX_BYTES) {
      reject(name, `File exceeds ${MAX_BYTES / (1024 * 1024)} MB limit`, result.errors);
      continue;
    }
    if (name.includes("/") || name.includes("\\") || name.startsWith(".")) {
      reject(name, "Invalid filename", result.errors);
      continue;
    }

    const target = `${dirField.replace(/\/+$/, "")}/${name}`;
    const abs = resolveSafe(target);
    if (!abs) {
      reject(name, "Forbidden path", result.errors);
      continue;
    }

    try {
      await fs.mkdir(path.dirname(abs), { recursive: true });
      const buf = Buffer.from(await file.arrayBuffer());
      await fs.writeFile(abs, buf);
      result.uploaded.push({ path: target, size: buf.byteLength });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      reject(name, msg, result.errors);
    }
  }

  const status = result.uploaded.length === 0 ? 400 : 200;
  return NextResponse.json(result, { status });
}

export const dynamic = "force-dynamic";
