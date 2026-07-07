import { act, renderHook } from "@testing-library/react";
import { useThreadsPanel } from "@/app/hooks/useThreadsPanel";

const PINNED_KEY = "nr-threads-pinned";

describe("useThreadsPanel", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts closed and unpinned with no stored preference", () => {
    const { result } = renderHook(() => useThreadsPanel());
    expect(result.current.open).toBe(false);
    expect(result.current.pinned).toBe(false);
  });

  it("starts open and pinned when the preference is stored (restore on visit)", () => {
    localStorage.setItem(PINNED_KEY, "1");
    const { result } = renderHook(() => useThreadsPanel());
    expect(result.current.open).toBe(true);
    expect(result.current.pinned).toBe(true);
  });

  // Regression test for the dead top-bar icon: opening used to be reverted by
  // a threadId-coupled auto-close effect. No such path exists anymore — an
  // opened panel stays open until something explicitly closes it.
  it("toggle opens a closed panel and it stays open", () => {
    const { result, rerender } = renderHook(() => useThreadsPanel());
    act(() => result.current.toggle());
    expect(result.current.open).toBe(true);
    rerender();
    expect(result.current.open).toBe(true);
  });

  it("toggle on an open pinned panel closes and unpins", () => {
    localStorage.setItem(PINNED_KEY, "1");
    const { result } = renderHook(() => useThreadsPanel());
    act(() => result.current.toggle());
    expect(result.current.open).toBe(false);
    expect(result.current.pinned).toBe(false);
    expect(localStorage.getItem(PINNED_KEY)).toBe("0");
  });

  it("close unpins and persists the preference", () => {
    localStorage.setItem(PINNED_KEY, "1");
    const { result } = renderHook(() => useThreadsPanel());
    act(() => result.current.close());
    expect(result.current.open).toBe(false);
    expect(result.current.pinned).toBe(false);
    expect(localStorage.getItem(PINNED_KEY)).toBe("0");
  });

  it("togglePin flips and persists pinned without touching open", () => {
    const { result } = renderHook(() => useThreadsPanel());
    act(() => result.current.toggle());

    act(() => result.current.togglePin());
    expect(result.current.pinned).toBe(true);
    expect(result.current.open).toBe(true);
    expect(localStorage.getItem(PINNED_KEY)).toBe("1");

    act(() => result.current.togglePin());
    expect(result.current.pinned).toBe(false);
    expect(result.current.open).toBe(true);
    expect(localStorage.getItem(PINNED_KEY)).toBe("0");
  });

  it("onThreadSelected closes an unpinned panel", () => {
    const { result } = renderHook(() => useThreadsPanel());
    act(() => result.current.toggle());
    act(() => result.current.onThreadSelected());
    expect(result.current.open).toBe(false);
  });

  it("onThreadSelected keeps a pinned panel open", () => {
    localStorage.setItem(PINNED_KEY, "1");
    const { result } = renderHook(() => useThreadsPanel());
    act(() => result.current.onThreadSelected());
    expect(result.current.open).toBe(true);
    expect(result.current.pinned).toBe(true);
  });
});
