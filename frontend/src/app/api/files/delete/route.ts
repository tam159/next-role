import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import { resolveSafe } from "../_lib";

export async function DELETE(req: NextRequest) {
  const url = new URL(req.url);
  const repoRel = url.searchParams.get("path");
  if (!repoRel) {
    return NextResponse.json({ error: "Missing 'path'" }, { status: 400 });
  }
  const abs = resolveSafe(repoRel);
  if (!abs) {
    return NextResponse.json({ error: "Forbidden path" }, { status: 403 });
  }
  try {
    await fs.unlink(abs);
    return NextResponse.json({ ok: true });
  } catch (err: unknown) {
    const code = (err as { code?: string })?.code;
    if (code === "ENOENT") {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    if (code === "EISDIR") {
      return NextResponse.json({ error: "Path is a directory" }, { status: 400 });
    }
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

export const dynamic = "force-dynamic";
