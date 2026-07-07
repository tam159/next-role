import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Environment convention: `.test.ts` files run in node (pure modules, route
// handlers, fetch wrappers); `.test.tsx` files run in jsdom (anything that
// renders or touches window/localStorage). The extension is the switch — a
// `.tsx` test without JSX (e.g. config.browser.test.tsx) is fine.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    globals: true,
    projects: [
      {
        extends: true,
        test: { name: "node", environment: "node", include: ["src/**/*.test.ts"] },
      },
      {
        extends: true,
        test: {
          name: "jsdom",
          environment: "jsdom",
          include: ["src/**/*.test.tsx"],
          setupFiles: ["./vitest.setup.ts"],
        },
      },
    ],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.*", "src/components/ui/**", "src/app/types/**", "src/types/**"],
      reporter: ["text", "html"],
    },
  },
});
