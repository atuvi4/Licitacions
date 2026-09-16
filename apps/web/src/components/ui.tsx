"use client";

/**
 * Barril de compatibilitat (handoff L2): els components viuen a `./ui/`.
 *
 * Mentre els consumidors anteriors no passen cap acció als seus buits,
 * `EmptyState` d'aquest barril és la variant tolerant; la versió estricta
 * (acció obligatòria al tipus) és `@/components/ui/EmptyState`. A L15, quan
 * cap buit quedi sense sortida, aquest barril exporta l'estricta i prou.
 */
export * from "./ui/index";
export { LegacyEmptyState as EmptyState } from "./ui/EmptyState";
