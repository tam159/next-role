import path from "node:path";
import { AGENT_FILE_SOURCES } from "@/app/config/agentFiles";

export const REPO_ROOT = path.resolve(process.cwd(), "..");

const BINARY_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "ico",
  "pdf",
  "zip",
  "gz",
  "tar",
  "mp3",
  "mp4",
  "wav",
  "ogg",
  "woff",
  "woff2",
  "ttf",
  "otf",
]);

export const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"]);

export function extOf(p: string): string {
  return p.split(".").pop()?.toLowerCase() ?? "";
}

export function isBinaryPath(p: string): boolean {
  return BINARY_EXTS.has(extOf(p));
}

export function encodingFor(p: string): "utf-8" | "base64" {
  return isBinaryPath(p) ? "base64" : "utf-8";
}

/**
 * Resolve a repo-relative path and ensure it stays inside an allowed
 * <root>/<includeDir> bucket from any registered agent config. Returns the
 * absolute path on success, or null if the path escapes the allowlist or
 * doesn't match any registered disk root.
 */
export function resolveSafe(repoRel: string): string | null {
  if (!repoRel || typeof repoRel !== "string") return null;
  const normalized = repoRel.replace(/^\/+/, "");
  const abs = path.resolve(REPO_ROOT, normalized);

  for (const cfg of Object.values(AGENT_FILE_SOURCES)) {
    const disk = cfg.disk;
    if (!disk) continue;
    const rootAbs = path.resolve(REPO_ROOT, disk.root);
    for (const dir of disk.includeDirs) {
      const allowed = path.resolve(rootAbs, dir);
      if (abs === allowed || abs.startsWith(allowed + path.sep)) {
        return abs;
      }
    }
  }
  return null;
}

/** Resolve a directory bucket (root + includeDir) to an absolute path. */
export function resolveDir(root: string, dir: string): string | null {
  const rootAbs = path.resolve(REPO_ROOT, root);
  if (rootAbs !== REPO_ROOT && !rootAbs.startsWith(REPO_ROOT + path.sep)) {
    return null;
  }
  for (const cfg of Object.values(AGENT_FILE_SOURCES)) {
    if (cfg.disk?.root !== root) continue;
    if (!cfg.disk.includeDirs.includes(dir)) continue;
    return path.resolve(rootAbs, dir);
  }
  return null;
}
