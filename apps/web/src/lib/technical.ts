/**
 * Solució tècnica d'una candidatura: el contracte d'S3 i com es llegeix.
 *
 * La pantalla és del requisit, no del catàleg de solucions: cada fila és un
 * requisit del plec i, si algú l'ha assenyalat, la solució que hi respon. Un
 * requisit sense solució és una decisió pendent d'una persona, no un error, i
 * aquí no es converteix mai en cap avís (C-11).
 *
 * Res d'aquest fitxer no calcula la preparació final: els problemes els diu S6.
 */

import type { Requirement } from "@/lib/requirements";
import { ROLE_LABELS } from "@/lib/labels";
import type { WorkspaceDetail } from "@/lib/workspace";

export type TechnicalSolutionStatus = "not_started" | "drafting" | "needs_validation" | "validated";
/** Els estats que una persona pot escriure. `validated` només s'hi arriba signant. */
export type TechnicalSolutionWorkStatus = Exclude<TechnicalSolutionStatus, "validated">;
export type TechnicalSolutionSource = "requirement" | "manual";
export type StaleReason = "content_changed" | "requirement_changed" | "requirement_missing";

export interface TechnicalSolution {
  id: string;
  workspace_id: string;
  requirement_id: string | null;
  title: string;
  proposed_solution: string | null;
  supplier_name: string | null;
  product_name: string | null;
  owner_id: string | null;
  status: TechnicalSolutionStatus;
  source: TechnicalSolutionSource;
  notes: string | null;
  validated_by: string | null;
  validated_at: string | null;
  validated_note: string | null;
  validated_requirement_fingerprint: string | null;
  validated_content_fingerprint: string | null;
  /** Derivat pel servidor en llegir. No és cap cinquè estat. */
  validation_stale: boolean;
  validation_stale_reasons: string[];
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface TechnicalSolutionCreateResult {
  /** `false` vol dir que el requisit ja en tenia una i s'ha obert la que hi era. */
  created: boolean;
  solution: TechnicalSolution;
}

/** El que el client pot enviar a `PATCH`. Ni `source`, ni `requirement_id`, ni la firma. */
export interface TechnicalSolutionPatch {
  title?: string;
  proposed_solution?: string | null;
  supplier_name?: string | null;
  product_name?: string | null;
  owner_id?: string | null;
  notes?: string | null;
  status?: TechnicalSolutionWorkStatus;
}

/* ------------------------------------------------------------------------- */
/* Etiquetes                                                                  */
/* ------------------------------------------------------------------------- */

export const SOLUTION_STATUS_LABELS: Record<TechnicalSolutionStatus, string> = {
  not_started: "Sense començar",
  drafting: "En redacció",
  needs_validation: "Cal validar",
  validated: "Validada",
};

export const SOLUTION_STATUS_LEVEL: Record<TechnicalSolutionStatus, "neutral" | "running" | "attention" | "ready"> = {
  not_started: "neutral",
  drafting: "running",
  needs_validation: "attention",
  validated: "ready",
};

/** Els estats que es poden triar en editar, en l'ordre en què avança la feina. */
export const WORK_STATUSES: readonly TechnicalSolutionWorkStatus[] = ["not_started", "drafting", "needs_validation"];

/** Per què una validació ha deixat de valer. Els codis són els d'S3, i cap més. */
export const STALE_REASON_LABELS: Record<StaleReason, string> = {
  content_changed: "La solució ha canviat des de la validació",
  requirement_changed: "El requisit ha canviat des de la validació",
  requirement_missing: "El requisit vinculat ja no està disponible",
};

export function staleReasonLabel(code: string): string {
  return (STALE_REASON_LABELS as Record<string, string>)[code] ?? code;
}

/* ------------------------------------------------------------------------- */
/* Permisos                                                                   */
/* ------------------------------------------------------------------------- */

/** `SOLUTION_EDIT_ROLES` del backend. Validar i revisar l'abast tenen la mateixa llista. */
export const SOLUTION_EDIT_ROLES = ["owner", "admin", "bid_manager", "technical"] as const;

export function canEditSolutions(role: string | null | undefined): boolean {
  return role !== null && role !== undefined && (SOLUTION_EDIT_ROLES as readonly string[]).includes(role);
}

/** «qui té el rol de …», per a un `PermissionNote`. Rol, mai nom de persona. */
export function whoCanEditSolutions(): string {
  const labels = SOLUTION_EDIT_ROLES.map((role) => ROLE_LABELS[role]);
  return `qui té el rol de ${labels.slice(0, -1).join(", ")} o ${labels[labels.length - 1]}`;
}

/* ------------------------------------------------------------------------- */
/* Abast                                                                      */
/* ------------------------------------------------------------------------- */

/**
 * Quins requisits de l'anàlisi vigent formen part d'aquesta candidatura.
 *
 * És la mateixa regla que aplica el servidor (`technical_scope.in_scope`):
 * - candidatura d'un lot: els generals i els d'aquell lot;
 * - candidatura global: els generals i els dels lots on ens presentem, que
 *   només els diu l'oferta econòmica. Sense oferta, cap lot: només els generals.
 * Un plec sense lots no té requisits de lot, així que la regla hi val tal qual.
 */
export function requirementsInScope(
  requirements: readonly Requirement[],
  workspace: Pick<WorkspaceDetail, "tender_lot_id">,
  selectedLotIds: ReadonlySet<string>,
): Requirement[] {
  return requirements.filter((requirement) => {
    if (requirement.tender_lot_id === null) return true;
    if (workspace.tender_lot_id !== null) return requirement.tender_lot_id === workspace.tender_lot_id;
    return selectedLotIds.has(requirement.tender_lot_id);
  });
}

/** Cert quan l'abast pot dependre de l'oferta: candidatura global i requisits de lot. */
export function scopeDependsOnOffer(
  requirements: readonly Requirement[],
  workspace: Pick<WorkspaceDetail, "tender_lot_id">,
): boolean {
  return workspace.tender_lot_id === null && requirements.some((requirement) => requirement.tender_lot_id !== null);
}

/* ------------------------------------------------------------------------- */
/* Files                                                                      */
/* ------------------------------------------------------------------------- */

/**
 * Una fila de la taula. Sempre hi ha requisit o solució; les dues alhora és el
 * cas normal.
 */
export interface RequirementRow {
  key: string;
  requirement: Requirement | null;
  solution: TechnicalSolution | null;
}

/**
 * El requisit d'aquesta solució ja no es pot recuperar.
 *
 * Ho diu el servidor, no la pantalla: o bé el vincle s'ha buidat —que és
 * exactament la condició amb què S6 emet `technical_solution_requirement_missing`—
 * o bé la caducitat de la validació ho declara. Que un requisit no surti a
 * l'abast d'ara NO vol dir això: vol dir que és història.
 */
export function isRequirementMissing(solution: TechnicalSolution): boolean {
  if (solution.source !== "requirement") return false;
  return solution.requirement_id === null || solution.validation_stale_reasons.includes("requirement_missing");
}

export interface RowGroup {
  key: string;
  label: string;
  rows: RequirementRow[];
}

/**
 * Requisit → la seva solució. S3 garanteix com a màxim una solució lligada per
 * requisit i candidatura, així que aquí no hi ha res a triar.
 *
 * Les solucions que no casen amb cap requisit de l'abast se separen en dos
 * munts que NO són el mateix: les que el servidor diu que han perdut el
 * requisit, i les que apunten a un requisit que existeix però que ara no és a
 * l'abast. Aquestes segones són història i no demanen res.
 */
export function joinRequirements(
  inScope: readonly Requirement[],
  allRequirements: readonly Requirement[],
  solutions: readonly TechnicalSolution[],
): { rows: RequirementRow[]; missing: RequirementRow[]; outOfScope: RequirementRow[]; manual: TechnicalSolution[] } {
  const byId = new Map(allRequirements.map((requirement) => [requirement.id, requirement]));
  const inScopeIds = new Set(inScope.map((requirement) => requirement.id));

  const byRequirement = new Map<string, TechnicalSolution>();
  const manual: TechnicalSolution[] = [];
  const missing: RequirementRow[] = [];
  const outOfScope: RequirementRow[] = [];

  for (const solution of solutions) {
    if (solution.source === "manual") {
      manual.push(solution);
      continue;
    }
    if (isRequirementMissing(solution)) {
      missing.push({ key: `missing-${solution.id}`, requirement: null, solution });
      continue;
    }
    const requirementId = solution.requirement_id;
    if (requirementId === null) {
      // `source = requirement` sense vincle i sense que el servidor ho digui:
      // no s'hi pot afirmar res, i es conserva sense inventar-ne el motiu.
      outOfScope.push({ key: `orphan-${solution.id}`, requirement: null, solution });
      continue;
    }
    if (inScopeIds.has(requirementId)) {
      byRequirement.set(requirementId, solution);
      continue;
    }
    outOfScope.push({ key: `out-${solution.id}`, requirement: byId.get(requirementId) ?? null, solution });
  }

  const rows = inScope.map((requirement) => ({
    key: requirement.id,
    requirement,
    solution: byRequirement.get(requirement.id) ?? null,
  }));

  return { rows, missing, outOfScope, manual };
}

/**
 * Grups per categoria del requisit, en l'ordre en què el servidor les envia
 * (categoria i data). La categoria és una dada real de l'anàlisi; cap altra
 * agrupació ho seria.
 */
export function groupRows(rows: readonly RequirementRow[], labelFor: (category: string) => string): RowGroup[] {
  const groups = new Map<string, RowGroup>();
  for (const row of rows) {
    const category = row.requirement?.category ?? "__missing__";
    let group = groups.get(category);
    if (!group) {
      group = { key: category, label: row.requirement ? labelFor(category) : "Requisit no disponible", rows: [] };
      groups.set(category, group);
    }
    group.rows.push(row);
  }
  return [...groups.values()];
}

/* ------------------------------------------------------------------------- */
/* Filtres                                                                    */
/* ------------------------------------------------------------------------- */

export type RowFilter = "all" | "mine" | "unsolved" | "to_validate";

export const ROW_FILTERS: readonly { key: RowFilter; label: string }[] = [
  { key: "all", label: "Totes" },
  { key: "mine", label: "Em toquen a mi" },
  { key: "unsolved", label: "Sense solució" },
  { key: "to_validate", label: "Per validar" },
];

/** Una solució que espera una persona: pendent de validar, o validada i caducada. */
export function awaitsValidation(solution: TechnicalSolution): boolean {
  return solution.status === "needs_validation" || (solution.status === "validated" && solution.validation_stale);
}

export function applyFilter(rows: readonly RequirementRow[], filter: RowFilter, userId: string | null): RequirementRow[] {
  switch (filter) {
    case "all":
      return [...rows];
    case "mine":
      return rows.filter((row) => row.solution !== null && row.solution.owner_id !== null && row.solution.owner_id === userId);
    case "unsolved":
      return rows.filter((row) => row.requirement !== null && row.solution === null);
    case "to_validate":
      return rows.filter((row) => row.solution !== null && awaitsValidation(row.solution));
  }
}

/* ------------------------------------------------------------------------- */
/* Edició                                                                     */
/* ------------------------------------------------------------------------- */

/** Els camps del formulari, sempre com a text: el `null` del servidor és cadena buida aquí. */
export interface SolutionDraft {
  title: string;
  proposed_solution: string;
  supplier_name: string;
  product_name: string;
  owner_id: string;
  notes: string;
  status: TechnicalSolutionWorkStatus | "validated";
}

export function draftFrom(solution: TechnicalSolution): SolutionDraft {
  return {
    title: solution.title,
    proposed_solution: solution.proposed_solution ?? "",
    supplier_name: solution.supplier_name ?? "",
    product_name: solution.product_name ?? "",
    owner_id: solution.owner_id ?? "",
    notes: solution.notes ?? "",
    status: solution.status,
  };
}

/**
 * Només el que ha canviat, i mai res que el servidor tingui en propietat.
 *
 * Els camps opcionals buits viatgen com a `null`, que és com el servidor
 * distingeix «esborra-ho» de «no ho toquis» (`UNSET`). `validated` no s'hi pot
 * escriure: si l'esborrany diu que ho és, l'estat no s'envia.
 */
export function diffDraft(original: TechnicalSolution, draft: SolutionDraft): TechnicalSolutionPatch {
  const patch: TechnicalSolutionPatch = {};
  const orNull = (value: string) => (value.trim() === "" ? null : value);
  if (draft.title !== original.title) patch.title = draft.title;
  if (orNull(draft.proposed_solution) !== original.proposed_solution) patch.proposed_solution = orNull(draft.proposed_solution);
  if (orNull(draft.supplier_name) !== original.supplier_name) patch.supplier_name = orNull(draft.supplier_name);
  if (orNull(draft.product_name) !== original.product_name) patch.product_name = orNull(draft.product_name);
  if (orNull(draft.owner_id) !== original.owner_id) patch.owner_id = orNull(draft.owner_id);
  if (orNull(draft.notes) !== original.notes) patch.notes = orNull(draft.notes);
  if (draft.status !== original.status && draft.status !== "validated") patch.status = draft.status;
  return patch;
}

export function isDirty(original: TechnicalSolution, draft: SolutionDraft): boolean {
  return Object.keys(diffDraft(original, draft)).length > 0;
}

/* ------------------------------------------------------------------------- */
/* Persones                                                                   */
/* ------------------------------------------------------------------------- */

/** Nom d'un membre, o una sortida honesta si l'id no és a l'equip. */
export function memberName(workspace: Pick<WorkspaceDetail, "members">, userId: string | null): string | null {
  if (!userId) return null;
  const person = workspace.members.find((member) => member.id === userId);
  if (!person) return "Membre no disponible";
  return person.full_name || person.email;
}
