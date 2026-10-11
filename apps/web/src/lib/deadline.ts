/**
 * El termini de presentació, comptat UNA sola vegada (addendum 3B-2, A11/A12).
 *
 * Al pilot la mateixa licitació deia «12 dies» a la fitxa i a Oportunitats i
 * «11» a Candidatures: dos arrodoniments (cap amunt al web, cap avall al
 * servidor) sobre blocs de 24 hores. La regla única és la que entén qui prepara
 * una oferta: **dies naturals sencers que queden a Europe/Madrid**, l'hora
 * oficial de les plataformes de contractació. Falten tants dies com dies de
 * calendari separen avui del dia del termini; l'hora no hi compta, i el canvi
 * d'hora tampoc.
 *
 * I una segona regla, independent de l'estat cru de la font: si l'instant del
 * termini ja ha passat, és «Fora de termini», encara que sigui avui mateix i
 * encara que la font continuï publicant «en termini». L'estat de la font es
 * mostra a part (`tenderStatusView`), mai com a estat vigent.
 */

import type { Level } from "@/components/ui/StatusPill";

export const DEADLINE_TIME_ZONE = "Europe/Madrid";

const DAY_MS = 86_400_000;

const CALENDAR_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: DEADLINE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function parseInstant(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** El dia natural a Madrid d'un instant, com a mitjanit UTC: dues restes exactes. */
function calendarDay(instant: Date): number {
  const parts = CALENDAR_DAY.formatToParts(instant);
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value);
  return Date.UTC(part("year"), part("month") - 1, part("day"));
}

/**
 * Dies naturals sencers que queden fins al termini, a Europe/Madrid. 0 vol dir
 * «avui»; negatiu, dies des que va vèncer. `null` sense termini (o il·legible).
 */
export function deadlineCountdown(deadline: string | Date | null | undefined, now: Date = new Date()): number | null {
  const target = parseInstant(deadline);
  if (!target) return null;
  return Math.round((calendarDay(target) - calendarDay(now)) / DAY_MS);
}

/** Cert si l'instant del termini ja ha passat. Sense termini, mai. */
export function isDeadlinePassed(deadline: string | Date | null | undefined, now: Date = new Date()): boolean {
  const target = parseInstant(deadline);
  return target !== null && target.getTime() < now.getTime();
}

/**
 * Nivell del comptador de dies: a menys d'una setmana bloqueja; a tres setmanes
 * demana atenció. Un termini ja passat NO és cap urgència: és neutre (el vermell
 * queda per als terminis imminents de licitacions obertes).
 */
export function deadlineLevel(days: number | null): Level {
  if (days === null || days < 0) return "neutral";
  if (days <= 7) return "blocking";
  if (days <= 21) return "attention";
  return "neutral";
}

export interface DeadlineCountdown {
  days: number | null;
  passed: boolean;
  /** «Fora de termini» · «Venç avui» · «1 dia» · «N dies» · «Sense termini». */
  text: string;
  level: Level;
}

/** El comptador tal com es pinta a totes les pantalles: mateix nombre, mateix text. */
export function deadlineCountdownView(deadline: string | Date | null | undefined, now: Date = new Date()): DeadlineCountdown {
  const days = deadlineCountdown(deadline, now);
  if (days === null) return { days: null, passed: false, text: "Sense termini", level: "neutral" };
  // Passat: pastilla neutra/apagada, mai vermella (el vermell és per als imminents).
  if (isDeadlinePassed(deadline, now)) return { days, passed: true, text: "Fora de termini", level: "neutral" };
  const text = days === 0 ? "Venç avui" : `${days} ${days === 1 ? "dia" : "dies"}`;
  return { days, passed: false, text, level: deadlineLevel(days) };
}
