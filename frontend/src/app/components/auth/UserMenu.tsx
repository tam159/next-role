"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { authClient } from "@/lib/auth/client";
import { isAuthEnabled } from "@/lib/auth/enabled";

/**
 * Signed-in user chip with a sign-out popover, for the top bar. Renders
 * nothing in zero-login mode or while no session is loaded.
 */
export function UserMenu() {
  if (!isAuthEnabled()) return null;
  return <UserMenuInner />;
}

function UserMenuInner() {
  const router = useRouter();
  const { data: session } = authClient.useSession();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!session) return null;
  const { user } = session;
  const initial = (user.name || user.email || "?").charAt(0).toUpperCase();

  const handleSignOut = async () => {
    setOpen(false);
    await authClient.signOut();
    router.replace("/login");
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={user.email}
        aria-haspopup="menu"
        aria-expanded={open}
        className="grid size-[38px] place-items-center rounded-[10px] border border-transparent transition-colors hover:bg-surface3"
      >
        <span className="grid size-[26px] place-items-center rounded-full bg-brand-accent text-[12px] font-bold text-on-accent">
          {initial}
        </span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute top-[46px] right-0 z-50 w-[228px] rounded-[14px] border border-primary bg-surface-raised p-1.5 shadow-[var(--shadow-lg)]"
        >
          <div className="flex min-w-0 flex-col px-2.5 pt-2 pb-1.5">
            <span className="truncate text-[13.5px] font-semibold text-primary">
              {user.name || "Signed in"}
            </span>
            <span className="truncate text-xs text-secondary">{user.email}</span>
          </div>
          <button
            role="menuitem"
            onClick={handleSignOut}
            className="flex w-full items-center gap-2 rounded-[10px] px-2.5 py-2 text-left text-[13.5px] font-medium text-primary transition-colors hover:bg-surface3"
          >
            <LogOut className="size-4 text-secondary" strokeWidth={1.7} />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
