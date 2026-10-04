import { Card, CardHeader, LoadingRows, StatusPill } from "@/components/ui";
import { explanation, label, levelOf, type AnalysisRun, type DeliveryCandidate } from "@/lib/analysis";
import { PARTNER_ROLE_LABELS } from "@/lib/knowledge";

import type { AnalysisLookup } from "./AnalysisTab";
import { scopeItemLabel } from "./Citation";

function who(candidate: DeliveryCandidate, lookup: AnalysisLookup): string | null {
  const service = candidate.company_service_id ? (lookup.services.get(candidate.company_service_id)?.name ?? null) : null;
  const role = candidate.partner_role_id ? lookup.partnerRoles.get(candidate.partner_role_id) : undefined;
  const partner = role ? [role.partner, PARTNER_ROLE_LABELS[role.role] ?? null].filter(Boolean).join(" · ") || null : null;
  // Amb servei i soci alhora: servei · soci · paper. Altrament, l'únic que hi ha.
  if (service && partner) return `${service} · ${partner}`;
  return service ?? partner ?? null;
}

export function Delivery({ run, lookup }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const items = lookup.structure?.scope_items ?? [];
  const byItem = new Map<string, DeliveryCandidate[]>();
  for (const candidate of run.delivery_candidates) byItem.set(candidate.scope_item_id, [...(byItem.get(candidate.scope_item_id) ?? []), candidate]);
  // Tots els elements de l'estructura (també els que no tenen cap candidat) i, a més,
  // els que només surten als candidats (estructura no carregada o desfasada).
  const ids = [...items.map((s) => s.id), ...[...byItem.keys()].filter((id) => !items.some((s) => s.id === id))];
  return (
    <Card id="execucio-analisi">
      <CardHeader title="Execució" description="Qui podria executar cada part de l'abast segons el coneixement actual. No és cap disseny de solució ni cap selecció de proveïdors." />
      {ids.length === 0 && !lookup.structure && !lookup.structureFailed ? (
        // Encara es carrega: no s'afirma res (ni buit ni error).
        <LoadingRows rows={2} />
      ) : ids.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">
          {lookup.structure ? "El plec no té elements d'abast estructurats." : "No s'ha pogut carregar l'estructura del plec."}
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {ids.map((id) => {
            const item = items.find((s) => s.id === id);
            const block = item?.block_id ? lookup.structure?.blocks.find((b) => b.id === item.block_id) : undefined;
            const site = item?.site_id ? lookup.structure?.sites.find((s) => s.id === item.site_id) : undefined;
            const title = scopeItemLabel(lookup, id);
            return (
              <li key={id} className="space-y-2 px-5 py-3.5">
                <p className="text-body font-medium text-ink">{title}</p>
                {(block || site) && (
                  <p className="text-caption text-muted">{[block && `${block.code} · ${block.name}`, site && (site.name ?? site.code)].filter(Boolean).join(" — ")}</p>
                )}
                {(byItem.get(id) ?? []).length === 0 && (
                  <p className="text-body text-muted">Cap candidat d&apos;execució en aquesta anàlisi.</p>
                )}
                <ul className="space-y-1.5">
                  {(byItem.get(id) ?? []).map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-2 text-body">
                      <StatusPill level={levelOf("candidate_type", c.candidate_type)} size="sm">{label("candidate_type", c.candidate_type)}</StatusPill>
                      {who(c, lookup) && <span className="text-ink">{who(c, lookup)}</span>}
                      <StatusPill level={levelOf("candidate_readiness", c.readiness)} size="sm" outline>{label("candidate_readiness", c.readiness)}</StatusPill>
                      <span className="text-caption text-muted">{label("territory_verdict", c.territory_verdict)}</span>
                      <span className="w-full text-caption text-muted">{explanation(c.explanation_code, run.rules_version)}</span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
