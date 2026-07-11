import { render, screen } from "@testing-library/react";
import { SessionGate } from "./SessionGate";
import { authClient } from "@/lib/auth/client";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("@/lib/auth/client", () => ({
  authClient: {
    useSession: vi.fn(),
  },
}));

const useSessionMock = vi.mocked(authClient.useSession);

type SessionState = ReturnType<typeof authClient.useSession>;

function setSession(state: Partial<SessionState>) {
  useSessionMock.mockReturnValue(state as SessionState);
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("SessionGate", () => {
  it("renders children untouched in zero-login mode", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    render(
      <SessionGate>
        <div>app content</div>
      </SessionGate>
    );
    expect(screen.getByText("app content")).toBeInTheDocument();
    expect(useSessionMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows loading and does not redirect while the session is pending", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    setSession({ data: null, isPending: true });
    render(
      <SessionGate>
        <div>app content</div>
      </SessionGate>
    );
    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByText("app content")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects to /login when unauthenticated", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    setSession({ data: null, isPending: false });
    render(
      <SessionGate>
        <div>app content</div>
      </SessionGate>
    );
    expect(replaceMock).toHaveBeenCalledWith("/login");
    expect(screen.queryByText("app content")).not.toBeInTheDocument();
  });

  it("renders children when a session exists", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    setSession({
      data: { user: { id: "u1", name: "Ada", email: "ada@example.com" } },
      isPending: false,
    } as unknown as Partial<SessionState>);
    render(
      <SessionGate>
        <div>app content</div>
      </SessionGate>
    );
    expect(screen.getByText("app content")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
