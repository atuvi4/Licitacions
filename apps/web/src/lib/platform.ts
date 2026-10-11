/**
 * Administració Tuvi (consola de plataforma): tipus de l'API `/platform`,
 * etiquetes i petits càlculs de presentació. Cap dada d'aquí surt de les rutes
 * d'empresa: l'operador no hi té accés.
 */

import type { Level } from "@/components/ui";
import type { WorkspaceStatus } from "@/lib/workspace";

export type PlatformStatus = "pilot" | "active" | "paused";

export const PLATFORM_STATUS_LABELS: Record<PlatformStatus, string> = {
  pilot: "Pilot",
  active: "Activa",
  paused: "Pausada",
};

export const PLATFORM_STATUS_LEVEL: Record<PlatformStatus, Level> = {
  pilot: "running",
  active: "ready",
  paused: "neutral",
};

export interface PlatformPerson {
  id: string;
  full_name: string | null;
  email: string;
}

export interface PlatformOwner extends PlatformPerson {
  assigned_at: string;
  last_login_at: string | null;
}

export interface PlatformCompanyRow {
  id: string;
  legal_name: string;
  trade_name: string | null;
  tax_id: string | null;
  platform_status: PlatformStatus;
  pilot_ends_at: string | null;
  owner: PlatformOwner | null;
  users_count: number;
  tenders_count: number;
  last_activity_at: string | null;
  created_at: string;
}

export interface PlatformCompanyList {
  items: PlatformCompanyRow[];
  counts: { all: number; pilot: number; active: number; paused: number };
}

export interface PlatformActivity {
  at: string;
  action: string;
  entity_type: string | null;
  summary: string | null;
  actor: PlatformPerson | null;
  by_platform: boolean;
}

export interface PlatformCompanyDetail {
  id: string;
  legal_name: string;
  trade_name: string | null;
  tax_id: string | null;
  created_at: string;
  onboarded_by: PlatformPerson | null;
  status: {
    platform_status: PlatformStatus;
    pilot_started_at: string | null;
    pilot_ends_at: string | null;
    status_changed_at: string | null;
  };
  owner: PlatformOwner | null;
  client_space: {
    tenders: { total: number; active: number; preparing: number; submitted: number };
    recent_tenders: Array<{ title: string; buyer_name: string | null; status: WorkspaceStatus; deadline_at: string | null }>;
    recent_activity: PlatformActivity[];
    users: { total: number; active_last_7_days: number };
  };
}

export interface CompanyOnboarded {
  company: {
    id: string;
    legal_name: string;
    trade_name: string | null;
    tax_id: string | null;
    platform_status: PlatformStatus;
    pilot_ends_at: string | null;
  };
  owner: { id: string; email: string; full_name: string | null };
  temporary_password: string;
}

export const PLATFORM_HOME = "/plataforma/empreses";
/** Licitacions públiques: l'entrada cap als documents públics oficials de cadascuna (3B-2 §7.1.5). */
export const PLATFORM_TENDERS = "/plataforma/licitacions";
export const DEFAULT_PILOT_DAYS = 60;

export function platformTenderDocumentsPath(tenderId: string): string {
  return `${PLATFORM_TENDERS}/${tenderId}/documents`;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Un identificador enganxat a la cerca porta directament a la licitació. */
export function looksLikeUuid(value: string): boolean {
  return UUID_PATTERN.test(value.trim());
}

/** Nom a mostrar: el comercial si n'hi ha, si no el legal. */
export function displayName(company: { legal_name: string; trade_name: string | null }): string {
  return company.trade_name?.trim() || company.legal_name;
}

/** Dues inicials per a l'avatar quadrat («Enginyeria Canigó» → «EC»). */
export function initials(name: string | null | undefined): string {
  // El punt volat és dins de la paraula («Instal·lacions»), no la parteix.
  const words = (name ?? "").replace(/·/g, "").replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

/** Data ISO (AAAA-MM-DD) d'avui + `days`, en hora local. */
export function isoDatePlus(days: number, from: Date = new Date()): string {
  const date = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** «fa 12 min», «fa 3 h», «ahir», «fa 2 dies»; més enllà d'una setmana, la data. */
export function relativeTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "—";
  const then = new Date(value);
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000);
  if (minutes < 1) return "ara mateix";
  if (minutes < 60) return `fa ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `fa ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "ahir";
  if (days < 7) return `fa ${days} dies`;
  return new Intl.DateTimeFormat("ca-ES", { day: "numeric", month: "short", year: "numeric" }).format(then);
}

/** «Dia 29 de 61» i el percentatge de la barra; `null` si no és un pilot amb dates. */
export function pilotProgress(
  startedAt: string | null,
  endsAt: string | null,
  today: Date = new Date(),
): { day: number; total: number; percent: number } | null {
  if (!startedAt || !endsAt) return null;
  const day0 = Date.UTC(...ymd(startedAt));
  const dayN = Date.UTC(...ymd(endsAt));
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const total = Math.max(1, Math.round((dayN - day0) / 86400000) + 1);
  const day = Math.min(total, Math.max(1, Math.round((now - day0) / 86400000) + 1));
  return { day, total, percent: Math.round((day / total) * 100) };
}

function ymd(value: string): [number, number, number] {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return [year, month - 1, day];
}
