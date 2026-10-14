import { cpvDescription, type TenderCPV } from "@/lib/tenders";

/**
 * Quin CPV de la fila ha fet coincidir el filtre (addendum 3B-2, C4).
 *
 * El servei filtra per prefix de dígits (com a molt els 8 del codi, sense el
 * de control) sobre els CPV de la licitació o de qualsevol lot vigent
 * (`_cpv_clause` a `tender_filters.py`). La fila només ensenya el primer CPV de la font:
 * si ha entrat per un secundari, o per un CPV d'un lot que la fila no porta,
 * s'ha de dir. Tot es calcula al client, amb els CPV que ja venen a la fila.
 */

export type CpvMatchKind = "main" | "secondary" | "lot_only";

export interface CpvMatch {
  kind: CpvMatchKind;
  cpv: TenderCPV | null;
}

/** El prefix que el servei aplica: només dígits, com a molt 8. `null` si no en queda cap. */
export function cpvFilterPrefix(filter: string | null | undefined): string | null {
  const digits = (filter ?? "").replace(/\D/g, "").slice(0, 8);
  return digits === "" ? null : digits;
}

export function cpvMatch(cpvs: TenderCPV[], filter: string | null | undefined): CpvMatch | null {
  const prefix = cpvFilterPrefix(filter);
  if (!prefix) return null;
  const hits = (cpv: TenderCPV) => cpv.cpv_code.replace(/\D/g, "").startsWith(prefix);
  const main = cpvs.find((cpv) => cpv.is_main && hits(cpv));
  if (main) return { kind: "main", cpv: main };
  const secondary = cpvs.find((cpv) => !cpv.is_main && hits(cpv));
  if (secondary) return { kind: "secondary", cpv: secondary };
  // El servei només pot haver-la inclòs per un CPV d'un lot, que la fila no porta.
  return { kind: "lot_only", cpv: null };
}

export function cpvMatchText(match: CpvMatch): string {
  if (match.kind === "main") return "Coincideix amb el filtre";
  if (match.kind === "lot_only") return "Coincideix amb el filtre per un CPV d'un lot";
  const cpv = match.cpv!;
  const description = cpvDescription(cpv);
  return `Coincideix amb el filtre per un altre CPV de la llista, ${cpv.cpv_code}${description ? ` · ${description}` : ""}`;
}
