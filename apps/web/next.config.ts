import type { NextConfig } from "next";

// 127.0.0.1 rather than localhost: uvicorn binds IPv4 only, and Node may resolve localhost to ::1.
const apiUrl =
  process.env.SYNTHA_API_URL ?? `http://127.0.0.1:${process.env.SYNTHA_API_PORT ?? "8100"}`;

const nextConfig: NextConfig = {
  // The browser only ever talks to its own origin; /api/* is proxied to FastAPI.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
