/**
 * UUID v4 amb aleatorietat criptogràfica, també fora d'un context segur.
 *
 * `crypto.randomUUID()` només existeix en un context segur (HTTPS o
 * `localhost`). Obrint l'aplicació per HTTP amb la IP de la xarxa local
 * (`http://192.168.x.y:3100`) el navegador no l'exposa, però sí
 * `crypto.getRandomValues()`, que és el mateix generador criptogràfic. Amb
 * aquest es construeix un UUID v4 segons l'RFC 9562 (abans RFC 4122).
 *
 * Mai `Math.random()`, ni marques de temps, ni comptadors: si no hi ha cap
 * font criptogràfica, es falla de manera explícita.
 *
 * `globalThis.crypto` es llegeix en cridar la funció, no en carregar el mòdul:
 * es pot importar des de codi que també es renderitza al servidor (Node també
 * té `globalThis.crypto`).
 */

export class SecureRandomUnavailableError extends Error {
  constructor() {
    super("Aquest navegador no ofereix aleatorietat criptogràfica per generar identificadors.");
    this.name = "SecureRandomUnavailableError";
  }
}

function webCrypto(): Crypto | undefined {
  return (globalThis as { crypto?: Crypto }).crypto;
}

/** Els 16 bytes marcats com a UUID v4 (versió) i variant RFC (10xx). */
export function formatUuidV4(bytes: Uint8Array): string {
  if (bytes.length !== 16) throw new RangeError("Un UUID necessita 16 bytes.");
  const b = Uint8Array.from(bytes);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = Array.from(b, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function browserUuid(): string {
  const crypto = webCrypto();
  if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  if (!crypto || typeof crypto.getRandomValues !== "function") {
    throw new SecureRandomUnavailableError();
  }
  return formatUuidV4(crypto.getRandomValues(new Uint8Array(16)));
}
