import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the floating Next.js dev indicator (the "N" button, bottom-left; dev
  // only). Compile/runtime errors are still reported. To show it again, set this
  // to `undefined` (or delete the line) and restart the frontend container.
  devIndicators: false,
  // `next build` also emits a self-contained server (.next/standalone) for the
  // production image (frontend/Dockerfile `prod` target). No effect on `next dev`.
  output: "standalone",
};

export default nextConfig;
