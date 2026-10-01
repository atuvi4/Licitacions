"use client";

import { Button, Card, CardHeader, EmptyState } from "@/components/ui";
import {
  FACT_LABELS,
  FLAG_EXPLANATIONS,
  FLAG_LABELS,
  candidateSearchText,
  capabilityLabel,
  certificationKeyTitle,
  certificationTitle,
  groupCandidates,
  groupPendingRecords,
  pendingCandidates,
  recordState,
  serviceLineLabel,
  type Observation,
  type PendingGroup,
} from "@/lib/knowledge";

import { CandidateRow } from "./CandidateRow";
import { KnowledgeItem } from "./KnowledgeItem";
import { NoResults } from "./NoResults";
import type { KnowledgeView } from "./view";

const ACTION = "min-h-11 md:min-h-0";

function pendingTitle(group: PendingGroup, view: KnowledgeView): { title: string; subtitle?: string } {
  const { records, vocabulary } = view;
  switch (group.subject) {
    case "certification": {
      const certification = view.certifications.find((item) => item.id === group.recordId);
      return { title: certification ? certificationTitle(certification, vocabulary) : certificationKeyTitle(group.key, vocabulary), subtitle: "Certificació" };
    }
    case "service": {
      const service = view.services.find((item) => item.id === group.recordId);
      return { title: service?.name ?? serviceLineLabel(group.key, vocabulary), subtitle: `Servei · ${serviceLineLabel(group.key, vocabulary)}` };
    }
    case "operating_fact":
      return { title: FACT_LABELS[group.key] ?? group.key, subtitle: "Fet operatiu" };
    case "solution_capability": {
      const capability = records.solution_capabilities.find((item) => item.id === group.recordId);
      const solution = records.solutions.find((item) => item.id === capability?.solution_id);
      return { title: capabilityLabel(group.key, vocabulary), subtitle: solution ? `Capacitat de ${solution.name}` : "Capacitat" };
    }
    case "solution":
      return { title: group.key, subtitle: "Solució" };
    case "product":
      return { title: group.key, subtitle: "Producte" };
    case "partner":
      return { title: group.key, subtitle: "Partner" };
    default:
      return { title: group.key };
  }
}

/**
 * «Pendents de validar»: la feina. A dalt, el que ja és actual però encara no
 * es pot acreditar (falta evidència, falta verificar, abast desconegut,
 * caducat); a sota, el que ha suggerit l'històric i ningú no ha revisat.
 */
export function PendingTab({ view, onShowCurrent }: { view: KnowledgeView; onShowCurrent: () => void }) {
  const allGroups = groupPendingRecords(view.snapshot);
  const allCandidates = pendingCandidates(view.snapshot)
    .map((item) => (item.observation_id ? view.observations.get(item.observation_id) : undefined))
    .filter((item): item is Observation => Boolean(item));
  const groups = allGroups.filter((group) => {
    const { title, subtitle } = pendingTitle(group, view);
    return view.matches(title, subtitle);
  });
  const candidates = allCandidates.filter((observation) => view.matches(...candidateSearchText(observation, view.vocabulary)));

  if (groups.length === 0 && candidates.length === 0 && allGroups.length + allCandidates.length > 0) {
    return <NoResults search={view.search} />;
  }
  if (groups.length === 0 && candidates.length === 0) {
    return (
      <Card>
        <EmptyState
          title="Res pendent de validar"
          description="No hi ha cap dada declarada sense acreditar ni cap suggeriment de l'històric per revisar."
          action={
            <Button variant="primary" size="md" onClick={onShowCurrent}>
              Declara coneixement
            </Button>
          }
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {groups.length > 0 && (
        <Card id="per-acreditar">
          <CardHeader
            title="Dades actuals per acreditar"
            description="Ja consten com a actuals, però encara no es poden acreditar del tot davant d'un òrgan de contractació."
          />
          <ul>
            {groups.map((group) => {
              const state = recordState(group.recordId, "current", view.index);
              const certification = group.subject === "certification" ? view.certifications.find((item) => item.id === group.recordId) : undefined;
              const { title, subtitle } = pendingTitle(group, view);
              return (
                <KnowledgeItem
                  key={group.id}
                  kind={group.subject}
                  recordId={group.recordId}
                  title={title}
                  subtitle={subtitle}
                  state={state}
                  canEdit={view.canEdit}
                  canVerify={view.canVerify}
                  onChanged={view.reload}
                  actions={
                    certification && group.kinds.includes("scope_unknown") ? (
                      <Button size="sm" variant="ghost" className={ACTION} disabled={!view.canEdit} onClick={() => view.editScope(certification)}>
                        Edita l&apos;abast
                      </Button>
                    ) : undefined
                  }
                >
                  <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-caption text-muted">
                    {state.flags.map((flag) => (
                      <li key={flag}>
                        <span className="font-medium text-ink">{FLAG_LABELS[flag]}.</span> {FLAG_EXPLANATIONS[flag]}
                      </li>
                    ))}
                    {state.state === "expired" && (
                      <li>
                        <span className="font-medium text-ink">Ha caducat.</span> No compta fins que se&apos;n renovi la data.
                      </li>
                    )}
                    {state.state === "in_progress" && (
                      <li>
                        <span className="font-medium text-ink">En tràmit.</span> No compta fins que sigui vigent.
                      </li>
                    )}
                  </ul>
                </KnowledgeItem>
              );
            })}
          </ul>
        </Card>
      )}

      {candidates.length > 0 && (
        <Card id="de-l-historic">
          <CardHeader
            title="Detectat a l'històric"
            description="L'històric suggereix, però no acredita res. Confirma el que continua sent cert, o descarta-ho. Res no es confirma sol."
          />
          <ul>
            {groupCandidates(candidates, view.superseded, true).map((group) => (
              <li key={group.key} data-candidates={group.key} className="border-b border-border last:border-b-0">
                <p className="bg-subtle px-5 py-2 font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">
                  {group.label} · {group.items.length}
                </p>
                <ul>
                  {group.items.map((observation) => (
                    <CandidateRow key={observation.id} observation={observation} view={view} />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
