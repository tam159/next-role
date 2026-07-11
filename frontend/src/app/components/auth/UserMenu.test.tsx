import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserMenu } from "./UserMenu";
import { authClient } from "@/lib/auth/client";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("@/lib/auth/client", () => ({
  authClient: {
    useSession: vi.fn(),
    signOut: vi.fn(),
  },
}));

const useSessionMock = vi.mocked(authClient.useSession);
const signOutMock = vi.mocked(authClient.signOut);

function setSession(data: unknown) {
  useSessionMock.mockReturnValue({ data, isPending: false } as ReturnType<
    typeof authClient.useSession
  >);
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("UserMenu", () => {
  it("renders nothing in zero-login mode", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing while no session is loaded", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    setSession(null);
    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the user's initial and signs out from the popover", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    setSession({ user: { id: "u1", name: "Ada Lovelace", email: "ada@example.com" } });
    signOutMock.mockResolvedValue(undefined as never);

    const user = userEvent.setup();
    render(<UserMenu />);

    expect(screen.getByText("A")).toBeInTheDocument();

    await user.click(screen.getByTitle("ada@example.com"));
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: /sign out/i }));
    expect(signOutMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});
