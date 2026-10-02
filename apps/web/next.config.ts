import type { NextConfig } from "next";

import { LEGACY_REDIRECTS } from "./src/lib/legacy-redirects";

// El navegador MAI parla directament amb FastAPI: totes les crides passen per
// aquest proxy same-origin. Aixi la cookie de sessio es httpOnly i first-party,
// i la URL interna de l'API no s'exposa mai al client.
const apiUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Pantalles 4C antigues que ja tenen una UI canonica a «Qualificacio i mitjans».
  async redirects() {
    return LEGACY_REDIRECTS;
  },
  async rewrites() {
    return [
      {
        source: "/bff/:path*",
        destination: `${apiUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
