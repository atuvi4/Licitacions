/** Text per comparar: sense accents, en minúscules i sense espais als extrems. */
export function normaliseText(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
}

/**
 * Un cercador senzill: la consulta, normalitzada una vegada, es busca dins de
 * qualsevol dels textos. Sense consulta, tot passa.
 */
export function searchMatcher(query: string): (...texts: Array<string | null | undefined>) => boolean {
  const needle = normaliseText(query);
  if (!needle) return () => true;
  return (...texts) => texts.some((text) => Boolean(text) && normaliseText(text as string).includes(needle));
}
