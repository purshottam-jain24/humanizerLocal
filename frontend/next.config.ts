import type { NextConfig } from "next";

// HumanWrite's UI is a client application talking to the local FastAPI backend,
// so server-side caching features (cacheComponents / partial prefetching) are not used.
const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
