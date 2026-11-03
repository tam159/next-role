import { render, screen } from "@testing-library/react";
import { ConfigDialog } from "@/app/components/ConfigDialog";

vi.mock("@/app/components/AppearanceSettings", () => ({ AppearanceSettings: () => null }));

const CONFIG = { deploymentUrl: "http://localhost:2024", assistantId: "career_agent" };

function renderDialog() {
  render(<ConfigDialog open onOpenChange={vi.fn()} onSave={vi.fn()} initialConfig={CONFIG} />);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("ConfigDialog connection settings", () => {
  it("shows the connection fields by default", () => {
    vi.stubEnv("NEXT_PUBLIC_CONNECTION_SETTINGS_ENABLED", undefined);
    renderDialog();

    expect(screen.getByLabelText("Deployment URL")).toHaveValue("http://localhost:2024");
    expect(screen.getByLabelText("Assistant ID")).toBeInTheDocument();
    expect(screen.getByLabelText(/LangSmith API Key/)).toBeInTheDocument();
  });

  it("hides them where the deployment pins its backend", () => {
    vi.stubEnv("NEXT_PUBLIC_CONNECTION_SETTINGS_ENABLED", "false");
    renderDialog();

    expect(screen.queryByText("Connection")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Deployment URL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/LangSmith API Key/)).not.toBeInTheDocument();
  });
});
