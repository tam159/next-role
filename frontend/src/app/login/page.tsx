"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LogoMark } from "@/app/components/LogoMark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authClient } from "@/lib/auth/client";
import { isAuthEnabled, isGoogleAuthEnabled } from "@/lib/auth/enabled";

/**
 * Login / signup page for multi-user mode. In zero-login mode it just
 * bounces back to the app.
 */
export default function LoginPage() {
  if (!isAuthEnabled()) return <RedirectHome />;
  return <LoginForm />;
}

function RedirectHome() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return null;
}

type Mode = "signin" | "signup";

function LoginForm() {
  const router = useRouter();
  const { data: session, isPending } = authClient.useSession();

  const [mode, setMode] = useState<Mode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already signed in → straight to the app.
  useEffect(() => {
    if (!isPending && session) router.replace("/");
  }, [isPending, session, router]);

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res =
        mode === "signin"
          ? await authClient.signIn.email({ email, password })
          : await authClient.signUp.email({ name, email, password });
      if (res.error) {
        setError(res.error.message ?? "Authentication failed");
      } else {
        router.replace("/");
      }
    } finally {
      setBusy(false);
    }
  };

  const handleGoogle = async () => {
    setError(null);
    await authClient.signIn.social({ provider: "google", callbackURL: "/" });
  };

  const toggleMode = () => {
    setMode((m) => (m === "signin" ? "signup" : "signin"));
    setError(null);
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-[400px]">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <LogoMark size={48} />
          <div>
            <h1 className="text-[22px] font-bold tracking-[-0.02em] text-primary">
              Welcome to NextRole
            </h1>
            <p className="mt-1 text-sm text-secondary">Sign in to start your interview prep</p>
          </div>
        </div>

        <div className="rounded-[14px] border border-primary bg-surface-raised p-5 shadow-[var(--shadow-lg)]">
          {isGoogleAuthEnabled() && (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={handleGoogle}
                className="h-[38px] w-full gap-2.5 rounded-[10px]"
              >
                <GoogleIcon />
                Continue with Google
              </Button>
              <div className="my-4 flex items-center gap-3">
                <span className="h-px flex-1 bg-border2" />
                <span className="text-xs text-tertiary">or</span>
                <span className="h-px flex-1 bg-border2" />
              </div>
            </>
          )}

          <form onSubmit={handleEmailSubmit} className="flex flex-col gap-3">
            {mode === "signup" && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="auth-name">Name</Label>
                <Input
                  id="auth-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  required
                />
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="auth-email">Email</Label>
              <Input
                id="auth-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="auth-password">Password</Label>
              <Input
                id="auth-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                minLength={8}
                required
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              disabled={busy}
              className="mt-1 h-[38px] w-full rounded-[10px]"
            >
              {busy ? "Please wait..." : mode === "signin" ? "Sign in" : "Create account"}
            </Button>
          </form>

          <p className="mt-4 text-center text-sm text-secondary">
            {mode === "signin" ? "New to NextRole?" : "Already have an account?"}{" "}
            <button
              type="button"
              onClick={toggleMode}
              className="font-medium text-brand-accent hover:underline"
            >
              {mode === "signin" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </div>
      </div>
    </main>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4">
      <path
        fill="#4285F4"
        d="M23.5 12.27c0-.85-.08-1.66-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.39 3.62v3h3.87c2.26-2.09 3.57-5.17 3.57-8.81Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.87-3c-1.07.72-2.45 1.14-4.06 1.14-3.12 0-5.77-2.11-6.71-4.95H1.29v3.1A11.99 11.99 0 0 0 12 24Z"
      />
      <path
        fill="#FBBC05"
        d="M5.29 14.28A7.23 7.23 0 0 1 4.91 12c0-.79.14-1.56.38-2.28v-3.1H1.29a12 12 0 0 0 0 10.76l4-3.1Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.77c1.76 0 3.34.6 4.58 1.79l3.44-3.44C17.94 1.19 15.23 0 12 0A11.99 11.99 0 0 0 1.29 6.62l4 3.1C6.23 6.88 8.88 4.77 12 4.77Z"
      />
    </svg>
  );
}
