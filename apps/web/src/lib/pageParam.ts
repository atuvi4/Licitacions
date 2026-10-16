/** `?page=` vàlid del visor de documents (P2-07): un enter ≥ 1. Qualsevol altra cosa és nul·la. */
export function parsePageParam(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return value >= 1 ? value : null;
}
