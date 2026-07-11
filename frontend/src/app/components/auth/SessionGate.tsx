"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { authClient } from "@/lib/auth/client";
import { isAuthEnabled } from "@/lib/auth/enabled";

/**
 * Gates the app behind a session when multi-user auth is enabled; renders
 * children untouched in zero-login mode. Unauthenticated visitors are sent
 * to /login.
 */
export function SessionGate({ children }: { children: React.ReactNode }) {
  if (!isAuthEnabled()) return <>{children}</>;
  return <AuthedGate>{children}</AuthedGate>;
}

function AuthedGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: session, isPending } = authClient.useSession();

  useEffect(() => {
    if (!isPending && !session) router.replace("/login");
  }, [isPending, session, router]);

  if (!session) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }
  return <>{children}</>;
}
