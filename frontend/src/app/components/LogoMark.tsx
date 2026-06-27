import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * NextRole brand logo (rocket + upward "N"). Served from /public. Keeps a
 * `size`/`className` API so call sites (top bar, hero, assistant avatar) don't
 * need to change.
 */
export function LogoMark({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <Image
      src="/next-role-logo.png"
      alt="NextRole"
      width={size}
      height={size}
      className={cn("shrink-0 object-contain", className)}
    />
  );
}
