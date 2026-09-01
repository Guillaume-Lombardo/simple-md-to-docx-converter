import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  globalIgnores([
    ".next/**",
    "build/**",
    "coverage/**",
    "next-env.d.ts",
    "out/**",
    "src/api/generated/**",
    "tests/fixtures/generated/**",
  ]),
]);
