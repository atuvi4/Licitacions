import { Building2, CalendarDays, FileCheck, Search, type LucideIcon } from "lucide-react";

/**
 * Els quatre destins del shell (handoff L4): tres de treball i la secció
 * Empresa, que desplega tres subdestins quan és activa. Els recomptes que
 * s'ensenyen al costat els calcula `counts.ts`; aquí només es diu quin toca.
 */

export type NavCountKey = "avui" | "oportunitats" | "candidatures" | "perfil";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  countKey: NavCountKey;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAVIGATION_GROUPS: NavGroup[] = [
  {
    label: "Treball",
    items: [
      // L5 crea `/avui`; fins llavors el destí és el dashboard actual perquè
      // cap flux es perdi (contracte 0.2 del handoff).
      { href: "/dashboard", label: "Avui", icon: CalendarDays, countKey: "avui" },
      { href: "/licitacions", label: "Oportunitats", icon: Search, countKey: "oportunitats" },
      { href: "/candidatures", label: "Candidatures", icon: FileCheck, countKey: "candidatures" },
    ],
  },
  {
    label: "Empresa",
    items: [{ href: "/empresa", label: "El nostre perfil", icon: Building2, countKey: "perfil" }],
  },
];

/** Subnavegació de la secció Empresa (barra lateral i `ProfileSubnav`). */
export const PROFILE_SUBNAV: Array<{ href: string; label: string }> = [
  { href: "/empresa", label: "Totes les àrees" },
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

/** Dins de la secció, cada subdestí marca només el seu prefix. */
export function isSubnavActive(pathname: string, href: string): boolean {
  return underPrefix(pathname, href);
}
