import { defineConfig } from "vitest/config";
import { readProjectVersion } from "./project-version.mjs";

export default defineConfig({
  define: {
    "process.env.NEXT_PUBLIC_MARKWEAVE_VERSION":
      JSON.stringify(readProjectVersion()),
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["components/**/*.tsx", "src/**/*.ts", "src/**/*.mjs"],
      exclude: ["src/api/generated/**"],
      thresholds: { lines: 90, branches: 90, functions: 90, statements: 90 },
      reporter: ["text", "json-summary"],
    },
  },
});
