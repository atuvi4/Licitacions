import { Building2, CalendarDays, FileCheck, Search, type LucideIcon } from "lucide-react";

/**
 * Els quatre destins del shell (handoff L4): tres de treball i la secció
 * Empresa, que desplega els seus subdestins quan és activa. Els recomptes que
 * s'ensenyen al costat els calcula `counts.ts`; aquí només es diu quin toca.
 */

export type NavCountKey = "avui" | "oportunitats" | "candidatures";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Absent: el destí no porta cap recompte (el perfil ja no mostra cap percentatge, B1). */
  countKey?: NavCountKey;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAVIGATION_GROUPS: NavGroup[] = [
  {
    label: "Treball",
    items: [
      { href: "/avui", label: "Avui", icon: CalendarDays, countKey: "avui" },
      { href: "/licitacions", label: "Oportunitats", icon: Search, countKey: "oportunitats" },
      { href: "/candidatures", label: "Candidatures", icon: FileCheck, countKey: "candidatures" },
    ],
  },
  {
    // L'administració de la PRÒPIA empresa; la de la plataforma és l'«Administració Tuvi», fora de l'app.
    label: "Administració de l'empresa",
    items: [{ href: "/empresa", label: "El nostre perfil", icon: Building2 }],
  },
];

/** Subnavegació de la secció Empresa (barra lateral i `ProfileSubnav`). */
export const PROFILE_SUBNAV: Array<{ href: string; label: string }> = [
  { href: "/empresa", label: "Totes les àrees" },
  // Les preferències del Radar (CPV, territoris, franja d'import) a part del coneixement (addendum 3B-2, B4).
  { href: "/empresa/radar", label: "Preferències del Radar" },
  { href: "/empresa/coneixement", label: "Coneixement" },
  { href: "/documents", label: "Biblioteca de documents" },
  { href: "/experiencia", label: "Experiència" },
];

const PROFILE_PREFIXES = ["/empresa", "/experiencia", "/documents"];

function underPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** «El nostre perfil» també és actiu a `/experiencia` i `/documents`. */
export function isNavigationActive(pathname: string, href: string): boolean {
  if (href === "/empresa") return PROFILE_PREFIXES.some((prefix) => underPrefix(pathname, prefix));
  return underPrefix(pathname, href);
}

/**
 * Dins de la secció, cada subdestí marca només el seu prefix. «Totes les
 * àrees» cedeix a un subdestí més concret que també penja d'`/empresa`.
 */
export function isSubnavActive(pathname: string, href: string): boolean {
  if (!underPrefix(pathname, href)) return false;
  return !PROFILE_SUBNAV.some((item) => item.href !== href && item.href.startsWith(`${href}/`) && underPrefix(pathname, item.href));
}
