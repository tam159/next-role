import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "./page";
import { authClient } from "@/lib/auth/client";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("@/lib/auth/client", () => ({
  authClient: {
    useSession: vi.fn(),
    signIn: { email: vi.fn(), social: vi.fn() },
    signUp: { email: vi.fn() },
  },
}));

const useSessionMock = vi.mocked(authClient.useSession);
const signInEmailMock = vi.mocked(authClient.signIn.email);
const signUpEmailMock = vi.mocked(authClient.signUp.email);

function noSession() {
  useSessionMock.mockReturnValue({ data: null, isPending: false } as ReturnType<
    typeof authClient.useSession
  >);
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("LoginPage", () => {
  it("redirects home in zero-login mode", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "false");
    render(<LoginPage />);
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("redirects home when already signed in", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    useSessionMock.mockReturnValue({
      data: { user: { id: "u1" } },
      isPending: false,
    } as unknown as ReturnType<typeof authClient.useSession>);
    render(<LoginPage />);
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("signs in with email and password", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    noSession();
    signInEmailMock.mockResolvedValue({ data: {}, error: null } as never);

    const user = userEvent.setup();
    render(<LoginPage />);

    expect(screen.getByText("Welcome to NextRole")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(signInEmailMock).toHaveBeenCalledWith({
      email: "ada@example.com",
      password: "hunter2secret",
    });
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("shows the API error message on failed sign-in", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    noSession();
    signInEmailMock.mockResolvedValue({
      data: null,
      error: { message: "Invalid email or password" },
    } as never);

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("switches to signup mode and creates an account with a name", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    noSession();
    signUpEmailMock.mockResolvedValue({ data: {}, error: null } as never);

    const user = userEvent.setup();
    render(<LoginPage />);

    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create an account" }));

    await user.type(screen.getByLabelText("Name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2secret");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(signUpEmailMock).toHaveBeenCalledWith({
      name: "Ada Lovelace",
      email: "ada@example.com",
      password: "hunter2secret",
    });
  });

  it("offers Google sign-in only when the Google flag is on", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", "true");
    noSession();

    const { unmount } = render(<LoginPage />);
    expect(screen.queryByText("Continue with Google")).not.toBeInTheDocument();
    unmount();

    vi.stubEnv("NEXT_PUBLIC_AUTH_GOOGLE_ENABLED", "true");
    render(<LoginPage />);
    expect(screen.getByText("Continue with Google")).toBeInTheDocument();
  });
});
