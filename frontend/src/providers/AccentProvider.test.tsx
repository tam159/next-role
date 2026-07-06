import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ACCENT_STORAGE_KEY, AccentProvider, DEFAULT_ACCENT, useAccent } from "./AccentProvider";

function Probe() {
  const { accent, setAccent } = useAccent();
  return (
    <div>
      <span data-testid="accent">{accent}</span>
      <button onClick={() => setAccent("blue")}>set blue</button>
    </div>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-accent");
});

describe("AccentProvider", () => {
  it("defaults to the default accent when nothing is stored", () => {
    render(
      <AccentProvider>
        <Probe />
      </AccentProvider>
    );
    expect(screen.getByTestId("accent")).toHaveTextContent(DEFAULT_ACCENT);
  });

  it("hydrates a valid stored accent on mount", () => {
    window.localStorage.setItem(ACCENT_STORAGE_KEY, "coral");
    render(
      <AccentProvider>
        <Probe />
      </AccentProvider>
    );
    expect(screen.getByTestId("accent")).toHaveTextContent("coral");
  });

  it("ignores an invalid stored value", () => {
    window.localStorage.setItem(ACCENT_STORAGE_KEY, "neon");
    render(
      <AccentProvider>
        <Probe />
      </AccentProvider>
    );
    expect(screen.getByTestId("accent")).toHaveTextContent(DEFAULT_ACCENT);
  });

  it("setAccent updates state, the <html> attribute, and storage", async () => {
    const user = userEvent.setup();
    render(
      <AccentProvider>
        <Probe />
      </AccentProvider>
    );

    await user.click(screen.getByRole("button", { name: "set blue" }));

    expect(screen.getByTestId("accent")).toHaveTextContent("blue");
    expect(document.documentElement.getAttribute("data-accent")).toBe("blue");
    expect(window.localStorage.getItem(ACCENT_STORAGE_KEY)).toBe("blue");
  });
});

describe("useAccent", () => {
  it("throws when used outside AccentProvider", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow("useAccent must be used within AccentProvider");
    vi.restoreAllMocks();
  });
});
