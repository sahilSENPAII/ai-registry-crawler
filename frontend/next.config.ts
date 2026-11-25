import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // pin the root to this app so a lockfile in a parent folder is never taken as the workspace
  turbopack: { root: __dirname },
};

export default nextConfig;
