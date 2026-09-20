import type { NextConfig } from "next";
import { resolve } from "node:path";
import { readProjectVersion } from "./project-version.mjs";

const nextConfig: NextConfig = {
  env: { NEXT_PUBLIC_MARKWEAVE_VERSION: readProjectVersion() },
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  reactStrictMode: true,
  turbopack: { root: resolve(import.meta.dirname, "..") },
};

export default nextConfig;
