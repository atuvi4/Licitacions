/**
 * Pantalles 4C antigues que ja no són una via normal d'edició (fase 3A, pas 6).
 *
 * La UI canònica de qualificació és «Coneixement de l'empresa › Qualificació i
 * mitjans». Aquestes rutes editaven les mateixes dades amb una altra representació
 * (els serveis del catàleg com a línies d'un projecte, els límits d'una pòlissa un
 * a un), així que redirigeixen a la secció corresponent. Els components antics
 * queden com a codi legacy; no s'esborra cap dada.
 *
 * Només hi són les pantalles que gestionen EXACTAMENT les entitats de la secció
 * nova. `/empresa/solvencia` (també preferències de contractació) i `/empresa/equip`
 * (també titulacions de cada persona) gestionen conceptes que la secció nova no té.
 */

export const QUALIFICATION_PATH = "/empresa/coneixement";

export function qualificationHref(section: string): string {
  return `${QUALIFICATION_PATH}?tab=qualificacio&seccio=${section}`;
}

export interface LegacyRedirect {
  source: string;
  destination: string;
  /** Temporal (307): les rutes antigues poden tornar a tenir ús propi. */
  permanent: false;
}

export const LEGACY_REDIRECTS: LegacyRedirect[] = [
  { source: "/experiencia", destination: qualificationHref("experiencia"), permanent: false },
  // L'enllaç a un projecte concret obre el seu calaix (paràmetre `project`).
  {
    source: "/experiencia/:projectId",
    destination: `${qualificationHref("experiencia")}&project=:projectId`,
    permanent: false,
  },
  { source: "/empresa/assegurances", destination: qualificationHref("assegurances"), permanent: false },
  { source: "/empresa/registres", destination: qualificationHref("registres"), permanent: false },
];
