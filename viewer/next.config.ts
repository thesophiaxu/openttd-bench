import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The viewer is served on the tailnet via `tailscale serve`; without this the dev
  // client refuses cross-origin dev resources from that host and never hydrates.
  allowedDevOrigins: ["devvm-ubuntu.tail25b49.ts.net"],
};

export default nextConfig;
