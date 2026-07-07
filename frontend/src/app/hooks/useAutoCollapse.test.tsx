import { act, renderHook } from "@testing-library/react";
import { useAutoCollapse } from "@/app/hooks/useAutoCollapse";

function render(initialRunning: boolean, initialForce = false) {
  return renderHook(
    ({ isRunning, forceExpanded }: { isRunning: boolean; forceExpanded: boolean }) =>
      useAutoCollapse(isRunning, { forceExpanded }),
    { initialProps: { isRunning: initialRunning, forceExpanded: initialForce } }
  );
}

describe("useAutoCollapse", () => {
  it("mounts expanded while running", () => {
    const { result } = render(true);
    expect(result.current.isExpanded).toBe(true);
  });

  it("mounts collapsed when already terminal (history reload)", () => {
    const { result } = render(false);
    expect(result.current.isExpanded).toBe(false);
  });

  it("auto-collapses on the running → terminal transition", () => {
    const { result, rerender } = render(true);
    rerender({ isRunning: false, forceExpanded: false });
    expect(result.current.isExpanded).toBe(false);
  });

  it("re-expands on terminal → running (interrupt resume, reattach)", () => {
    const { result, rerender } = render(false);
    rerender({ isRunning: true, forceExpanded: false });
    expect(result.current.isExpanded).toBe(true);
  });

  it("keeps a user-expanded unit open through later transitions", () => {
    const { result, rerender } = render(false);
    act(() => result.current.toggle());
    expect(result.current.isExpanded).toBe(true);
    rerender({ isRunning: true, forceExpanded: false });
    rerender({ isRunning: false, forceExpanded: false });
    expect(result.current.isExpanded).toBe(true);
  });

  it("keeps a user-collapsed unit closed while it is still running", () => {
    const { result, rerender } = render(true);
    act(() => result.current.toggle());
    expect(result.current.isExpanded).toBe(false);
    rerender({ isRunning: false, forceExpanded: false });
    rerender({ isRunning: true, forceExpanded: false });
    expect(result.current.isExpanded).toBe(false);
  });

  it("forceExpanded pins the unit open and disables toggle", () => {
    const { result, rerender } = render(false, true);
    expect(result.current.isExpanded).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.isExpanded).toBe(true);
    // Releasing the pin returns to the machine's own state (collapsed) and
    // the ignored toggle did not count as a user override.
    rerender({ isRunning: false, forceExpanded: false });
    expect(result.current.isExpanded).toBe(false);
    rerender({ isRunning: true, forceExpanded: false });
    expect(result.current.isExpanded).toBe(true);
  });

  it("collapses at the end of an interrupt → resume → finish cycle", () => {
    // Interrupt pending: run paused (isRunning false), pinned open.
    const { result, rerender } = render(false, true);
    expect(result.current.isExpanded).toBe(true);
    // User approves: run resumes, pin released.
    rerender({ isRunning: true, forceExpanded: false });
    expect(result.current.isExpanded).toBe(true);
    // Run finishes: unit collapses.
    rerender({ isRunning: false, forceExpanded: false });
    expect(result.current.isExpanded).toBe(false);
  });
});
