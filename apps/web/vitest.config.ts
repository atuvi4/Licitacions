import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Proves unitàries del redisseny (handoff L0/L3): diccionari de nivells i
// components base. `@/` resol igual que a tsconfig.json.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // Vite 8 transforma amb oxc; el runtime automàtic de JSX evita importar React
  // a cada component (tsconfig deixa `jsx: preserve` per a Next).
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    // Un jsdom per worker en lloc d'un per fitxer: la suite triga segons, no minuts.
    pool: "vmThreads",
    css: false,
  },
});
