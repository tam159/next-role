import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { ChatContext, type ChatContextType } from "@/providers/ChatProvider";
import { useUploadCue } from "@/app/hooks/useUploadCue";

const DISMISSED_KEY = "nr-upload-cue-dismissed-v2";

type CueContext = { files: Record<string, string>; filesReady: boolean };

function renderCue(initial: Partial<CueContext> = {}) {
  let ctx: CueContext = { files: initial.files ?? {}, filesReady: initial.filesReady ?? true };
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ChatContext.Provider value={ctx as unknown as ChatContextType}>
      {children}
    </ChatContext.Provider>
  );
  const hook = renderHook(() => useUploadCue(), { wrapper });
  const setContext = (next: Partial<CueContext>) => {
    ctx = { ...ctx, ...next };
    hook.rerender();
  };
  return { ...hook, setContext };
}

describe("useUploadCue", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("hides both cues until filesReady (no first-paint flash)", () => {
    const { result, setContext } = renderCue({ filesReady: false });
    expect(result.current.showUploadCta).toBe(false);
    expect(result.current.showPulseCue).toBe(false);

    setContext({ filesReady: true });
    expect(result.current.showUploadCta).toBe(true);
    expect(result.current.showPulseCue).toBe(true);
  });

  it("treats agent-generated files as no uploads", () => {
    const { result } = renderCue({ files: { "/custom_resume/draft.md": "…" } });
    expect(result.current.hasUploads).toBe(false);
    expect(result.current.showUploadCta).toBe(true);
    expect(result.current.showPulseCue).toBe(true);
  });

  it("an /upload/ file hides the CTA but keeps the dot until a panel control is clicked", () => {
    const { result } = renderCue({ files: { "/upload/cv.pdf": "…" } });
    expect(result.current.hasUploads).toBe(true);
    expect(result.current.showUploadCta).toBe(false);
    // Uploading (e.g. from the hero card) must not retire the dot — the user
    // still needs pointing at where the next resume/JD goes.
    expect(result.current.showPulseCue).toBe(true);
    expect(localStorage.getItem(DISMISSED_KEY)).toBeNull();

    act(() => result.current.dismissCue());
    expect(result.current.showPulseCue).toBe(false);
    expect(localStorage.getItem(DISMISSED_KEY)).toBe("1");
  });

  it("dismissCue hides the dot, keeps the CTA, and persists", () => {
    const { result } = renderCue();
    act(() => result.current.dismissCue());
    expect(result.current.showPulseCue).toBe(false);
    expect(result.current.showUploadCta).toBe(true);
    expect(localStorage.getItem(DISMISSED_KEY)).toBe("1");
  });

  it("respects a stored dismissal on init", () => {
    localStorage.setItem(DISMISSED_KEY, "1");
    const { result } = renderCue();
    expect(result.current.showUploadCta).toBe(true);
    expect(result.current.showPulseCue).toBe(false);
  });
});
