import { filesApiUrl } from "@/app/lib/agentFiles";
import { authedFetch } from "@/lib/auth/token";

export type UploadResponse = {
  uploaded: { path: string; size: number }[];
  errors: { name: string; reason: string }[];
};

export async function uploadAgentFiles(args: {
  files: File[];
  targetDir: string;
}): Promise<UploadResponse> {
  const { files, targetDir } = args;
  const form = new FormData();
  form.append("path", targetDir);
  for (const f of files) form.append("file", f, f.name);

  const res = await authedFetch(filesApiUrl("/files/upload"), { method: "POST", body: form });
  const data = (await res.json().catch(() => null)) as UploadResponse | { error?: string } | null;

  if (!res.ok) {
    const reason =
      data && "error" in data && data.error ? data.error : `Upload failed (${res.status})`;
    throw new Error(reason);
  }
  return data as UploadResponse;
}

/** Virtual artifact path for user uploads (backend files API contract). */
export const CAREER_AGENT_UPLOAD_DIR = "/upload";

/** Accepted upload types — file-picker `accept` attribute and drag-drop filter. */
export const UPLOAD_ACCEPT = ".pdf,.doc,.docx,.txt,.md";

const UPLOAD_ACCEPT_EXTS = new Set(UPLOAD_ACCEPT.split(","));

/** True when a filename carries one of the accepted upload extensions. */
export function isAcceptedUploadName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return UPLOAD_ACCEPT_EXTS.has(name.slice(dot).toLowerCase());
}

export async function deleteAgentFile(virtualPath: string): Promise<void> {
  const res = await authedFetch(
    filesApiUrl(`/files/delete?path=${encodeURIComponent(virtualPath)}`),
    {
      method: "DELETE",
    }
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as { error?: string } | null;
    const reason = data?.error ?? `Delete failed (${res.status})`;
    throw new Error(reason);
  }
}
