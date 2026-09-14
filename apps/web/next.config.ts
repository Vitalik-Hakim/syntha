import type { NextConfig } from "next";

const apiUrl = process.env.SYNTHA_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // The browser only ever talks to its own origin; /api/* is proxied to FastAPI.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
