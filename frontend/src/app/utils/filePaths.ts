/**
 * Helpers for turning agent-written file paths in chat into clickable links.
 *
 * Matching is intentionally permissive, but linking is gated downstream on the
 * path resolving to a file that actually exists (see FilePreviewProvider) — so a
 * hallucinated or misspelled path (e.g. `/procesed/x.md`) renders as plain text,
 * never a broken link.
 */

// Sentinel scheme used to carry a candidate path from the remark plugin to the
// markdown <a> renderer without colliding with real URLs.
export const FILE_PATH_URL_PREFIX = "nextrole-file:";

// `/folder/.../name.ext` — requires a leading slash, at least one subfolder, and
// an extension. No spaces (spaced paths should be written in `backticks`, which
// are handled separately by the inline-code renderer).
const FILE_PATH_RE = /\/(?:[\w.-]+\/)+[\w.-]+\.[A-Za-z0-9]+/g;

/** Normalize a candidate path for comparison against known file keys. */
export function normalizeFilePath(candidate: string): string {
  let s = candidate.trim();
  s = s.replace(/^[`'"(<[]+/, "").replace(/[`'")>\].,;:!?]+$/, "");
  if (!s.startsWith("/")) s = `/${s}`;
  return s;
}

type MdNode = any;

function splitTextNode(value: string): MdNode[] {
  FILE_PATH_RE.lastIndex = 0;
  const nodes: MdNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = FILE_PATH_RE.exec(value)) !== null) {
    const start = m.index;
    const end = start + m[0].length;
    if (start > last) nodes.push({ type: "text", value: value.slice(last, start) });
    nodes.push({
      type: "link",
      url: FILE_PATH_URL_PREFIX + m[0],
      children: [{ type: "text", value: m[0] }],
    });
    last = end;
  }
  if (nodes.length === 0) return [{ type: "text", value }];
  if (last < value.length) nodes.push({ type: "text", value: value.slice(last) });
  return nodes;
}

/**
 * remark plugin: wrap bare file-path text in link nodes carrying the sentinel
 * scheme. Skips text already inside links / code so real URLs (autolinked by
 * remark-gfm, which runs first) and code spans are left untouched.
 */
export function remarkFilePaths() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node || !Array.isArray(node.children)) return;
      const out: MdNode[] = [];
      for (const child of node.children) {
        if (child.type === "text" && typeof child.value === "string") {
          out.push(...splitTextNode(child.value));
        } else {
          if (child.type !== "link" && child.type !== "inlineCode" && child.type !== "code") {
            walk(child);
          }
          out.push(child);
        }
      }
      node.children = out;
    };
    walk(tree);
  };
}
