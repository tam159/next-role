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

  const res = await fetch("/api/files/upload", { method: "POST", body: form });
  const data = (await res.json().catch(() => null)) as UploadResponse | { error?: string } | null;

  if (!res.ok) {
    const reason =
      data && "error" in data && data.error ? data.error : `Upload failed (${res.status})`;
    throw new Error(reason);
  }
  return data as UploadResponse;
}

export const CAREER_AGENT_UPLOAD_DIR = "backend/app/career_agent/upload/raw";
