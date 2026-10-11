/**
 * Administració Tuvi · documents públics oficials d'una licitació (3B-2 §2.6,
 * §7.1.5 punt 5): tipus del router `/platform/tenders/{id}/documents` i del de
 * classificació global, client HTTP i ajudants purs de presentació.
 *
 * Els badges d'origen, d'aportació i de retirada són els de Plecs
 * (`licitacions/[tenderId]/package.ts` i `PlecsOverview.tsx`): aquí no se'n
 * duplica cap. Cap dada d'aquí surt de les rutes d'empresa: l'operador només
 * entra a licitacions públiques (el backend respon `tender_not_public` si no).
 */

import { api } from "@/lib/api";
import {
  DOCUMENT_ROLE_LABELS,
  TENDER_DOCUMENT_LABELS,
  type RoleSource,
  type TenderDocumentMeta,
  type TenderDocumentType,
} from "@/lib/tenders";

/** `PlatformTenderDocumentRead`: la fila de Plecs amb tota la provenance. */
export interface PlatformTenderDocument extends TenderDocumentMeta {
  governing_source_id: string | null;
  contributed_by: string | null;
  contributed_at: string | null;
  contribution_source_url: string | null;
  verified_by: string | null;
  verified_at: string | null;
  verification_note: string | null;
  retired_by: string | null;
  retired_reason: string | null;
  created_at: string | null;
}

/** El rol que llegeix l'extracció (`EffectiveRoleRead`, resolutor amb `company_id=None`). */
export interface EffectiveRole {
  tender_document_id: string;
  role: string | null;
  document_type: TenderDocumentType;
  source: RoleSource;
  content_sha256: string | null;
  fallback_source: RoleSource | null;
}

/** `DocumentRoleReviewRead`: una revisió d'operador, només d'afegir. */
export interface RoleReview {
  id: string;
  tender_document_id: string;
  content_sha256: string;
  role: string;
  authority: string;
  actor_id: string;
  reason: string;
  based_on_run_id: string | null;
  based_on_suggestion_id: string | null;
  source_ref_id: string | null;
  supersedes_id: string | null;
  created_at: string;
}

export interface PlatformPackageGap {
  id: string;
  code: string;
  detail: Record<string, unknown> | null;
  would_provide: string[] | null;
}

/** `GlobalClassificationRead`: només el que la pantalla llegeix. */
export interface PlatformClassification {
  run: { id: string; classifier_version: string; document_count: number; created_at: string } | null;
  suggestions: unknown[];
  findings: unknown[];
  reviews: RoleReview[];
  effective_roles: EffectiveRole[];
  gaps: PlatformPackageGap[];
}

export function documentsPath(tenderId: string): string {
  return `/platform/tenders/${tenderId}/documents`;
}

export function classificationPath(tenderId: string): string {
  return `/platform/tenders/${tenderId}/classification`;
}

/* ------------------------------------------------------------------------- */
/* Què pot fer l'operador amb cada document (3B-2 §2.6, §2.4)                 */
/* ------------------------------------------------------------------------- */

export type OperatorAction = "verify" | "reject" | "retire" | "restore" | "hand_over";

export const OPERATOR_ACTION_LABELS: Record<OperatorAction, string> = {
  verify: "Verifica",
  reject: "Rebutja",
  retire: "Retira",
  restore: "Restaura",
  hand_over: "Cedeix al feed",
};

/** Només el que ha aportat o pujat l'operador es pot operar; el feed i les pujades de l'empresa, no. */
export function isOperatorManaged(document: Pick<PlatformTenderDocument, "origin">): boolean {
  return document.origin === "operator_url" || document.origin === "operator_upload";
}

/**
 * Les accions possibles, en l'ordre en què es pinten. Les regles són les del
 * servei (`public_document_intake`): un retirat només es restaura; un pendent
 * es verifica, es rebutja o es retira; un verificat es retira i, si és per URL,
 * es pot cedir al feed (el backend exigeix la prova que la font la publica);
 * un rebutjat només es retira.
 */
export function operatorActions(
  document: Pick<PlatformTenderDocument, "origin" | "contribution_status" | "removed_at">,
): OperatorAction[] {
  if (!isOperatorManaged(document)) return [];
  if (document.removed_at) return ["restore"];
  switch (document.contribution_status) {
    case "pending_verification":
      return ["verify", "reject", "retire"];
    case "verified":
      return document.origin === "operator_url" ? ["retire", "hand_over"] : ["retire"];
    default:
      return ["retire"];
  }
}

/** Revisar el rol exigeix un fitxer vigent: la revisió s'ancora al seu `sha256`. */
export function canReviewRole(
  document: Pick<PlatformTenderDocument, "analyzable" | "removed_at" | "latest_file">,
): boolean {
  return document.analyzable && !document.removed_at && Boolean(document.latest_file?.sha256);
}

export function effectiveRoleOf(
  classification: Pick<PlatformClassification, "effective_roles"> | null | undefined,
  documentId: string,
): EffectiveRole | null {
  return classification?.effective_roles.find((role) => role.tender_document_id === documentId) ?? null;
}

export const ROLE_OPTIONS = Object.entries(DOCUMENT_ROLE_LABELS).map(([value, label]) => ({ value, label }));

export const DOCUMENT_TYPE_OPTIONS = (Object.keys(TENDER_DOCUMENT_LABELS) as TenderDocumentType[]).map((value) => ({
  value,
  label: TENDER_DOCUMENT_LABELS[value],
}));

/* ------------------------------------------------------------------------- */
/* Client                                                                     */
/* ------------------------------------------------------------------------- */

export interface AddByUrlBody {
  url: string;
  source_page_url?: string | null;
  name?: string | null;
  document_type?: TenderDocumentType;
}

export interface RoleReviewBody {
  role: string;
  reason: string;
  content_sha256: string;
}

export const platformDocuments = {
  list: (tenderId: string) => api.get<PlatformTenderDocument[]>(documentsPath(tenderId)),
  classification: (tenderId: string) => api.get<PlatformClassification>(classificationPath(tenderId)),
  addByUrl: (tenderId: string, body: AddByUrlBody) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/by-url`, body),
  upload: (tenderId: string, form: FormData) =>
    api.upload<PlatformTenderDocument>(`${documentsPath(tenderId)}/upload`, form),
  verify: (tenderId: string, documentId: string, note: string | null = null) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/${documentId}/verify`, { note }),
  reject: (tenderId: string, documentId: string, reason: string) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/${documentId}/reject`, { reason }),
  retire: (tenderId: string, documentId: string, reason: string) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/${documentId}/retire`, { reason }),
  restore: (tenderId: string, documentId: string) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/${documentId}/restore`, {}),
  handOver: (tenderId: string, documentId: string, reason: string) =>
    api.post<PlatformTenderDocument>(`${documentsPath(tenderId)}/${documentId}/hand-over-to-feed`, { reason }),
  reviewRole: (tenderId: string, documentId: string, body: RoleReviewBody) =>
    api.post<PlatformClassification>(`${documentsPath(tenderId)}/${documentId}/role-review`, body),
};
