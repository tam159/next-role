import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveSafe } from "../_lib";

export async function PUT(req: NextRequest) {
  let body: { path?: string; content?: string; encoding?: "utf-8" | "base64" };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const { path: repoRel, content, encoding = "utf-8" } = body;
  if (!repoRel || typeof content !== "string") {
    return NextResponse.json({ error: "Missing 'path' or 'content'" }, { status: 400 });
  }
  const abs = resolveSafe(repoRel);
  if (!abs) {
    return NextResponse.json({ error: "Forbidden path" }, { status: 403 });
  }
  try {
    await fs.mkdir(path.dirname(abs), { recursive: true });
    if (encoding === "base64") {
      await fs.writeFile(abs, Buffer.from(content, "base64"));
    } else {
      await fs.writeFile(abs, content, "utf-8");
    }
    return NextResponse.json({ ok: true });
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message ?? err) }, { status: 500 });
  }
}

export const dynamic = "force-dynamic";
