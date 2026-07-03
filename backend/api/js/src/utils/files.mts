export function filterValidExportPath(
  path: string | { path: string } | undefined,
) {
  if (!path) return false;
  const p = typeof path === "string" ? path : path.path;
  return !p.split(":")[0].endsWith(".py");
}
