/**
 * Quan un import de la font es fa servir com a import (decisió del propietari, 19-10-2026).
 *
 * Regla única: un import econòmic (VEC, PBL sense IVA, PBL amb IVA; de la licitació o d'un lot)
 * només és **útil** si és estrictament més gran que 0. La PLACSP publica `0.00` en acords marc,
 * contractes centralitzats i altres casos sense pressupost propi: l'API el serveix cru (traçabilitat
 * amb l'XML), però la interfície mai no el fa servir com a import (ni xifra principal, ni línia de
 * PBL, ni màxim de l'oferta). És la mateixa regla que l'API (`app/tenders/amounts.py`), que filtra
 * i ordena amb ella.
 */

/** L'import tal com arriba si és útil (> 0); si no (nul, buit, 0, negatiu o no numèric), `null`. */
export function usefulAmount(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  if (text === "") return null;
  const number = Number(text);
  return Number.isFinite(number) && number > 0 ? String(value) : null;
}

/** Si la font l'ha publicat exactament a zero (`0`, `0.00`): no és «no consta», és un zero publicat. */
export function isPublishedZero(value: string | number | null | undefined): boolean {
  if (value === null || value === undefined) return false;
  const text = String(value).trim();
  return text !== "" && Number(text) === 0;
}

/** El fet del Resum quan la PLACSP publica l'import a 0,00 € (text aprovat, exacte). */
export const PLACSP_ZERO_AMOUNT_TEXT = "Publicat a 0,00 € a la PLACSP (no s'utilitza com a import econòmic)";

/** El mateix per a una licitació oberta a mà: el zero no ve de cap font. */
export const MANUAL_ZERO_AMOUNT_TEXT = "Entrat a 0,00 € (no s'utilitza com a import econòmic)";
