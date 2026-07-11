import {
  CAREER_AGENT_UPLOAD_DIR,
  deleteAgentFile,
  uploadAgentFiles,
  type UploadResponse,
} from "./uploadFiles";

function jsonResponse(data: unknown, init: { ok?: boolean; status?: number } = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => data,
  };
}

function nonJsonResponse(status: number) {
  return {
    ok: false,
    status,
    json: async () => {
      throw new SyntaxError("Unexpected token < in JSON");
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadAgentFiles", () => {
  const files = [
    new File(["abc"], "cv.pdf", { type: "application/pdf" }),
    new File(["defg"], "jd.txt", { type: "text/plain" }),
  ];

  it("POSTs FormData with the target dir and one entry per file, returning parsed JSON", async () => {
    const response: UploadResponse = {
      uploaded: [
        { path: `${CAREER_AGENT_UPLOAD_DIR}/cv.pdf`, size: 3 },
        { path: `${CAREER_AGENT_UPLOAD_DIR}/jd.txt`, size: 4 },
      ],
      errors: [],
    };
    const fetchMock = vi.fn(async () => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    const result = await uploadAgentFiles({ files, targetDir: CAREER_AGENT_UPLOAD_DIR });

    expect(result).toEqual(response);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/files/upload");
    expect(init.method).toBe("POST");

    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("path")).toBe(CAREER_AGENT_UPLOAD_DIR);
    const entries = body.getAll("file") as File[];
    expect(entries).toHaveLength(2);
    expect(entries.map((f) => f.name)).toEqual(["cv.pdf", "jd.txt"]);
    expect(entries.every((f) => f instanceof File)).toBe(true);
  });

  it("throws the server-provided error reason on a non-ok JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: "file too large" }, { ok: false, status: 413 }))
    );
    await expect(uploadAgentFiles({ files, targetDir: "some/dir" })).rejects.toThrow(
      "file too large"
    );
  });

  it("throws a generic message when the error response is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => nonJsonResponse(500))
    );
    await expect(uploadAgentFiles({ files, targetDir: "some/dir" })).rejects.toThrow(
      "Upload failed (500)"
    );
  });

  it("throws the generic message when the error JSON has no error field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({}, { ok: false, status: 422 }))
    );
    await expect(uploadAgentFiles({ files, targetDir: "some/dir" })).rejects.toThrow(
      "Upload failed (422)"
    );
  });
});

describe("deleteAgentFile", () => {
  it("DELETEs with the URL-encoded path in the query string", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(null));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteAgentFile("/upload/my cv.pdf")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      "/files/delete?path=%2Fupload%2Fmy%20cv.pdf",
      { method: "DELETE" }
    );
  });

  it("propagates the server-provided error reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: "forbidden" }, { ok: false, status: 403 }))
    );
    await expect(deleteAgentFile("/upload/cv.pdf")).rejects.toThrow("forbidden");
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => nonJsonResponse(404))
    );
    await expect(deleteAgentFile("/upload/cv.pdf")).rejects.toThrow("Delete failed (404)");
  });
});
