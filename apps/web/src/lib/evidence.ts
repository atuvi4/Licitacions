/**
 * Cites de pàgina deterministes: d'una selecció (o d'un fragment escrit) a
 * `char_start` / `char_end` exactes sobre el text DESAT de la pàgina.
 *
 * El backend talla el text amb índexs de Python (punts de codi). El navegador
 * compta en unitats UTF-16: fora del pla bàsic (p. ex. un emoji o un símbol
 * matemàtic) un caràcter són DUES unitats. Per això tot passa per
 * `toCodePoints`, i qualsevol cas ambigu (una selecció que talla un parell
 * subrogat, un text que no és el desat) falla tancat: sense cita, mai una cita
 * aproximada. El client no envia mai el text citat: el servidor el deriva.
 */

export interface EvidencePage {
  id: string;
  page_number: number;
  text: string;
}

export interface EvidenceDocument {
  documentId: string;
  name: string;
  /** «PPT · v1»: prou per distingir versions, sense cap identificador. */
  label: string;
  extractionId: string;
}

/** Una cita triada, pendent d'enviar. `quote` només és la previsualització. */
export interface CitationDraft {
  extractionId: string;
  pageId: string;
  pageNumber: number;
  documentName: string;
  charStart: number;
  charEnd: number;
  quote: string;
}

/** El que accepta l'API (`ExtractedPageCitation`). Sense text: només posicions. */
export function citationPayload(draft: CitationDraft) {
  return {
    kind: "extracted_page" as const,
    extraction_id: draft.extractionId,
    page_id: draft.pageId,
    char_start: draft.charStart,
    char_end: draft.charEnd,
  };
}

function isHigh(code: number) {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLow(code: number) {
  return code >= 0xdc00 && code <= 0xdfff;
}

/** Índex UTF-16 → índex en punts de codi. `undefined` si talla un parell subrogat. */
export function toCodePoints(text: string, utf16: number): number | undefined {
  if (utf16 < 0 || utf16 > text.length) return undefined;
  if (utf16 > 0 && utf16 < text.length && isHigh(text.charCodeAt(utf16 - 1)) && isLow(text.charCodeAt(utf16))) {
    return undefined;
  }
  let points = 0;
  for (let i = 0; i < utf16; i += 1) {
    if (!(isLow(text.charCodeAt(i)) && i > 0 && isHigh(text.charCodeAt(i - 1)))) points += 1;
  }
  return points;
}

/** El tros [start, end) en punts de codi: el mateix que faria Python. */
export function sliceCodePoints(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join("");
}

export type SpanResult = { ok: true; charStart: number; charEnd: number; quote: string } | { ok: false; reason: string };

/**
 * Una selecció (en unitats UTF-16 sobre el text de la pàgina) a posicions de
 * servidor. Els espais i salts de línia dels extrems es treuen: mai el text de dins.
 */
export function spanFromSelection(text: string, utf16Start: number, utf16End: number): SpanResult {
  let start = Math.min(utf16Start, utf16End);
  let end = Math.max(utf16Start, utf16End);
  while (start < end && /\s/.test(text[start])) start += 1;
  while (end > start && /\s/.test(text[end - 1])) end -= 1;
  if (end <= start) return { ok: false, reason: "Selecciona el text que vols citar." };
  const charStart = toCodePoints(text, start);
  const charEnd = toCodePoints(text, end);
  if (charStart === undefined || charEnd === undefined) {
    return { ok: false, reason: "La selecció talla un caràcter especial. Amplia-la una mica." };
  }
  const quote = sliceCodePoints(text, charStart, charEnd);
  // Doble comprovació: el que tallaria el servidor és exactament el que hi ha seleccionat.
  if (quote !== text.slice(start, end)) {
    return { ok: false, reason: "No s'ha pogut calcular la posició exacta d'aquesta selecció." };
  }
  return { ok: true, charStart, charEnd, quote };
}

/**
 * Un fragment escrit (alternativa amb teclat a seleccionar): ha d'aparèixer
 * UNA sola vegada al text desat. Primer literal; si no, ignorant només la
 * quantitat d'espais i salts de línia, amb les posicions de l'original.
 */
export function spanFromFragment(text: string, fragment: string): SpanResult {
  const wanted = fragment.trim();
  if (wanted.length < 3) return { ok: false, reason: "Escriu almenys unes quantes paraules." };
  const exact = occurrences(text, wanted);
  if (exact.length === 1) return spanFromSelection(text, exact[0], exact[0] + wanted.length);
  if (exact.length > 1) return { ok: false, reason: `Aquest text hi apareix ${exact.length} vegades: allarga'l.` };

  // Ignorant només la quantitat d'espais: «a nivell 2 amb el» troba «a nivell 2\namb el».
  const map: number[] = [];
  let collapsed = "";
  for (let i = 0; i < text.length; i += 1) {
    if (/\s/.test(text[i])) {
      if (collapsed.endsWith(" ")) continue;
      collapsed += " ";
    } else {
      collapsed += text[i];
    }
    map.push(i);
  }
  const needle = wanted.replace(/\s+/g, " ");
  const loose = occurrences(collapsed, needle);
  if (loose.length === 0) return { ok: false, reason: "Aquest text no és a la pàgina." };
  if (loose.length > 1) return { ok: false, reason: `Aquest text hi apareix ${loose.length} vegades: allarga'l.` };
  const first = map[loose[0]];
  const last = map[loose[0] + needle.length - 1];
  return spanFromSelection(text, first, last + 1);
}

function occurrences(haystack: string, needle: string): number[] {
  const found: number[] = [];
  let from = 0;
  for (;;) {
    const index = haystack.indexOf(needle, from);
    if (index === -1) return found;
    found.push(index);
    from = index + 1;
  }
}

/** Per ensenyar la cita en una línia: espais col·lapsats. No és el que s'envia. */
export function previewQuote(quote: string): string {
  return quote.replace(/\s+/g, " ").trim();
}
