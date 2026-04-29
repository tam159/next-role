import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { REPO_ROOT, encodingFor, isBinaryPath, resolveDir } from "../_lib";

type Entry = {
  path: string;
  size: number;
  isBinary: boolean;
  modifiedAt: string;
};

async function walk(absDir: string, out: Entry[]): Promise<void> {
  let dirents;
  try {
    dirents = await fs.readdir(absDir, { withFileTypes: true });
  } catch (err: any) {
    if (err?.code === "ENOENT") return;
    throw err;
  }
  for (const entry of dirents) {
    const abs = path.join(absDir, entry.name);
    if (entry.isDirectory()) {
      await walk(abs, out);
    } else if (entry.isFile()) {
      const stat = await fs.stat(abs);
      const repoRel = "/" + path.relative(REPO_ROOT, abs).split(path.sep).join("/");
      out.push({
        path: repoRel,
        size: stat.size,
        isBinary: isBinaryPath(repoRel),
        modifiedAt: stat.mtime.toISOString(),
      });
    }
  }
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const root = url.searchParams.get("root");
  const dirsParam = url.searchParams.get("dirs");
  if (!root || !dirsParam) {
    return NextResponse.json({ error: "Missing 'root' or 'dirs'" }, { status: 400 });
  }
  const dirs = dirsParam
    .split(",")
    .map((d) => d.trim())
    .filter(Boolean);
  const out: Entry[] = [];
  for (const dir of dirs) {
    const abs = resolveDir(root, dir);
    if (!abs) {
      return NextResponse.json({ error: `Disallowed root/dir: ${root}/${dir}` }, { status: 403 });
    }
    await walk(abs, out);
  }
  out.sort((a, b) => a.path.localeCompare(b.path));
  return NextResponse.json({ files: out });
}

export const dynamic = "force-dynamic";
// keep encodingFor in module graph for consumers that import via this barrel
void encodingFor;
