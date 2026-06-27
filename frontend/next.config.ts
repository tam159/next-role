import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the floating Next.js dev indicator (the "N" button, bottom-left; dev
  // only). Compile/runtime errors are still reported. To show it again, set this
  // to `undefined` (or delete the line) and restart the frontend container.
  devIndicators: false,
};

export default nextConfig;
