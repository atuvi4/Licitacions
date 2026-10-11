import type { RowTint } from "@/components/ui/DataTable";
import { formatDate } from "@/lib/format";
import type { CompletenessStatus } from "@/lib/labels";
import type { CompletenessSection, ExpiryAlert } from "@/lib/types";

/**
 * El hub del perfil (handoff L8) llegeix `completeness.sections[]` tal com
 * l'envia l'API i només hi afegeix text fix per clau: on es va a omplir cada
 * àrea, un subtítol i «què en depèn» en genèric. Cap xifra de licitacions
 * (C-5): el backend no la dona i no s'inventa. El `score` que també envia
 * l'API no es mostra enlloc (addendum 3B-2, B1): el perfil no és un
 * formulari que es completa, és el que l'empresa declara.
 */

/** Ruta on s'edita cada àrea (les claus reals de `services/completeness.py`). */
export const SECTION_LINKS: Record<string, string> = {
  general: "/empresa/dades",
  services: "/empresa/serveis",
  territories: "/empresa/territoris",
  cpv: "/empresa/cpv",
  solvency: "/empresa/solvencia",
  certifications: "/empresa/certificacions",
  registrations: "/empresa/registres",
  insurances: "/empresa/assegurances",
  team: "/empresa/equip",
  experience: "/experiencia",
  documents: "/documents",
};

export interface AreaMeta {
  /** Nom de l'àrea tal com es mostra (l'API l'envia sense accents). */
  title: string;
  /** Sota el nom de l'àrea: què hi va. */
  subtitle: string;
  /** Columna «Què en depèn»: tipus de requisit, mai un recompte. */
  dependsOn: string;
  /** Verb de l'acció quan l'àrea no té cap dada. */
  add: string;
}

export const AREA_META: Record<string, AreaMeta> = {
  general: {
    title: "Dades generals",
    subtitle: "Raó social, NIF, contacte i domicili",
    dependsOn: "Dades identificatives de qualsevol oferta",
    add: "Omple les dades",
  },
  services: {
    title: "Serveis",
    subtitle: "Què feu, amb les vostres paraules",
    dependsOn: "Descripció de l'activitat a memòries i ofertes",
    add: "Afegeix serveis",
  },
  territories: {
    title: "Àmbit geogràfic",
    subtitle: "On us podeu presentar",
    dependsOn: "Encaix territorial de les oportunitats",
    add: "Tria territoris",
  },
  cpv: {
    title: "CPV",
    subtitle: "Codis amb què l'Administració classifica els contractes",
    dependsOn: "Encaix d'activitat de les oportunitats",
    add: "Tria codis CPV",
  },
  solvency: {
    title: "Solvència econòmica",
    subtitle: "Exercicis econòmics i franja d'import",
    dependsOn: "Requisits de solvència econòmica i financera",
    add: "Afegeix exercicis",
  },
  certifications: {
    title: "Certificacions",
    subtitle: "Qualitat, medi ambient, seguretat",
    dependsOn: "Requisits de certificació",
    add: "Afegeix certificacions",
  },
  registrations: {
    title: "Registres i classificacions",
    subtitle: "ROLECE, RELI, classificació empresarial",
    dependsOn: "Requisits de classificació i d'inscripció en registres",
    add: "Afegeix registres",
  },
  insurances: {
    title: "Assegurances",
    subtitle: "Pòlisses i límits",
    dependsOn: "Requisits d'assegurances",
    add: "Afegeix pòlisses",
  },
  team: {
    title: "Equip humà",
    subtitle: "Persones i titulacions",
    dependsOn: "Requisits de personal i de titulació",
    add: "Afegeix persones",
  },
  experience: {
    title: "Experiència",
    subtitle: "Contractes de referència",
    dependsOn: "Requisits d'experiència i solvència tècnica",
    add: "Afegeix projectes",
  },
  documents: {
    title: "Documents",
    subtitle: "Biblioteca d'evidències",
    dependsOn: "Evidències documentals de qualsevol requisit",
    add: "Puja documents",
  },
};

const FALLBACK_META: AreaMeta = {
  title: "",
  subtitle: "Àrea del perfil",
  dependsOn: "Requisits que la demanen",
  add: "Afegeix-hi dades",
};

export function areaMeta(key: string): AreaMeta {
  return AREA_META[key] ?? FALLBACK_META;
}

/** Títol accentuat de les àrees conegudes; per a una clau nova, l'etiqueta de l'API. */
export function areaTitle(section: Pick<CompletenessSection, "key" | "label">): string {
  return AREA_META[section.key]?.title || section.label;
}

/** «A, B, C, D i N més»: el bàner no ha de ser una llista de deu noms. */
export function listNames(names: string[], max = 4): string {
  if (names.length <= max) return names.join(", ");
  const rest = names.length - max;
  return `${names.slice(0, max).join(", ")} i ${rest} més`;
}

export function areaLink(key: string): string {
  return SECTION_LINKS[key] ?? "/empresa";
}

/**
 * Cap verb de «completar» (B1): afegir-ne quan no n'hi ha cap, revisar-les quan
 * n'hi ha. Una àrea amb dades declarades sense evidència no és cap tasca global
 * (F12): l'evidència la demana una licitació concreta.
 */
export function areaActionLabel(section: Pick<CompletenessSection, "key" | "status">): string {
  if (section.status === "empty") return areaMeta(section.key).add;
  return "Revisa";
}

/**
 * Les preferències del Radar (B4): el que el Radar fa servir per prioritzar
 * (CPV i territoris; la franja d'import viu dins de «solvency», que no es pot
 * partir). No són coneixement corporatiu i es mostren a part, a `/empresa/radar`.
 */
export const RADAR_SECTION_KEYS = ["territories", "cpv"] as const;

export function isRadarSection(key: string): boolean {
  return (RADAR_SECTION_KEYS as readonly string[]).includes(key);
}

export function splitRadarSections(sections: CompletenessSection[]): {
  radar: CompletenessSection[];
  knowledge: CompletenessSection[];
} {
  return {
    radar: sections.filter((section) => isRadarSection(section.key)),
    knowledge: sections.filter((section) => !isRadarSection(section.key)),
  };
}

export interface AreaGroup {
  key: "empty" | "with_data";
  label: string;
  rows: CompletenessSection[];
  tint?: RowTint;
  collapsed?: boolean;
}

const GROUP_ORDER: Array<Omit<AreaGroup, "rows"> & { statuses: CompletenessStatus[] }> = [
  // To neutre (C4): «Sense dada» és un fet, no una alarma ni una crida a completar.
  { key: "empty", label: "Sense dada", statuses: ["empty"] },
  // `partial` i `complete` del servidor són una mètrica de completitud (dada
  // sense evidència = parcial): aquí no es separen ni es diuen «incompletes» (F12).
  { key: "with_data", label: "Amb dades", statuses: ["partial", "complete"] },
];

/** Dos grups de fet: sense cap dada o amb dades; els grups buits no es pinten. */
export function groupAreas(sections: CompletenessSection[]): AreaGroup[] {
  return GROUP_ORDER.map(({ statuses, ...group }) => ({
    ...group,
    rows: sections.filter((section) => statuses.includes(section.status)),
  })).filter((group) => group.rows.length > 0);
}

export function emptyAreas(sections: CompletenessSection[]): {
  count: number;
  names: string[];
  first: CompletenessSection | null;
} {
  const empties = sections.filter((section) => section.status === "empty");
  return { count: empties.length, names: empties.map(areaTitle), first: empties[0] ?? null };
}

export function profileContext(companyName: string, areaCount: number, updatedAt: string | null): string {
  const base = `${companyName} · ${areaCount} ${areaCount === 1 ? "àrea" : "àrees"}`;
  return updatedAt ? `${base} · última modificació ${formatDate(updatedAt)}` : base;
}

/**
 * Peu de la taula: compta àrees de manera neutra (amb dades / sense dada), mai
 * un percentatge de completitud (B1) ni una llista de deures (F12).
 */
export function footerLine(sections: CompletenessSection[]): string {
  const empty = sections.filter((section) => section.status === "empty").length;
  const withData = sections.length - empty;
  if (empty === 0) return `Les ${sections.length} àrees tenen dades`;
  return `${withData} ${withData === 1 ? "àrea" : "àrees"} amb dades · ${empty} sense dada`;
}

function days(n: number): string {
  return `${n} ${n === 1 ? "dia" : "dies"}`;
}

/** «Caduca el {data} · en N dies» o «Va caducar el {data} · fa N dies», amb la data real. */
export function expiryLine(alert: Pick<ExpiryAlert, "expires_at" | "days_left">): string {
  const date = formatDate(alert.expires_at);
  if (alert.days_left === 0) return `Caduca avui, ${date}`;
  if (alert.days_left < 0) return `Va caducar el ${date} · fa ${days(Math.abs(alert.days_left))}`;
  return `Caduca el ${date} · en ${days(alert.days_left)}`;
}

/** `entity_type` tal com els construeix `services/dashboard.py`. */
export const EXPIRY_ROUTES: Record<string, string> = {
  company_certification: "/empresa/certificacions",
  company_insurance: "/empresa/assegurances",
  company_registration: "/empresa/registres",
  person_qualification: "/empresa/equip",
  document: "/documents",
};

export function expiryRoute(entityType: string): string {
  return EXPIRY_ROUTES[entityType] ?? "/empresa";
}
