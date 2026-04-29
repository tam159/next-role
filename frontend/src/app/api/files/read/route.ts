import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import { encodingFor, resolveSafe } from "../_lib";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const repoRel = url.searchParams.get("path");
  if (!repoRel) {
    return NextResponse.json({ error: "Missing 'path'" }, { status: 400 });
  }
  const abs = resolveSafe(repoRel);
  if (!abs) {
    return NextResponse.json({ error: "Forbidden path" }, { status: 403 });
  }
  const encoding = encodingFor(repoRel);
  try {
    if (encoding === "base64") {
      const buf = await fs.readFile(abs);
      return NextResponse.json({
        content: buf.toString("base64"),
        encoding: "base64",
      });
    }
    const text = await fs.readFile(abs, "utf-8");
    return NextResponse.json({ content: text, encoding: "utf-8" });
  } catch (err: any) {
    if (err?.code === "ENOENT") {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    return NextResponse.json({ error: String(err?.message ?? err) }, { status: 500 });
  }
}

export const dynamic = "force-dynamic";
