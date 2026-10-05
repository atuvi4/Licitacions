import { EvidenceRef } from "@/components/ui";
import { serviceLineLabel } from "@/lib/knowledge";
import type { AwardCriterion, Requirement } from "@/lib/requirements";

import type { AnalysisLookup } from "./AnalysisTab";

export function requirementTitle(lookup: AnalysisLookup, id: string | null): string | null {
  if (!id) return null;
  return lookup.requirements.get(id)?.title ?? "Requisit sense títol disponible";
}

export function criterionName(lookup: AnalysisLookup, id: string | null): string | null {
  if (!id) return null;
  return lookup.criteria.get(id)?.name ?? "Criteri sense nom disponible";
}

export function scopeItemLabel(lookup: AnalysisLookup, id: string | null): string | null {
  if (!id) return null;
  const item = lookup.structure?.scope_items.find((s) => s.id === id);
  return item ? serviceLineLabel(item.service_line_code, lookup.vocabulary) : "Element d'abast sense nom disponible";
}

/** La cita del plec, només si existeix: extreta (`evidence`) o manual (`citations`). */
export function Citation({ tenderId, requirement, criterion }: { tenderId: string; requirement?: Requirement; criterion?: AwardCriterion }) {
  const list = requirement?.evidence ?? criterion?.evidence ?? [];
  const evidence = list.find((e) => e.evidence_role === "primary") ?? list[0];
  if (evidence) {
    const href = evidence.tender_document_id
      ? `/licitacions/${tenderId}/documents/${evidence.tender_document_id}?page=${evidence.page_number}&extraction=${evidence.extraction_id}`
      : null;
    return <EvidenceRef compact documentName={evidence.document_name} pageNumber={evidence.page_number} href={href} />;
  }
  const manualList = requirement ? requirement.citations : criterion?.citations;
  const manual = manualList?.find((c) => c.document_name || c.location_label);
  if (!manual) return null;
  if (!manual.location_label) return <EvidenceRef compact documentName={manual.document_name} pageNumber={manual.page_number} href={null} />;
  // Ubicació dins del document («PCAP cl. 7.A»); la pàgina només si el backend la dona.
  const parts = [manual.document_name, manual.location_label];
  if (manual.page_number !== null && manual.page_number !== undefined) parts.push(`p. ${manual.page_number}`);
  return <span className="text-caption text-muted">{parts.filter(Boolean).join(" · ")}</span>;
}
