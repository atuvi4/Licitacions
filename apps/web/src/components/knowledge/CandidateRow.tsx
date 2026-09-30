"use client";

import { formatDate } from "@/lib/format";
import { candidateTitle, describeProposal, isPromotable, type KnowledgeHint, type Observation, type Vocabulary } from "@/lib/knowledge";

import { CandidateActions } from "./CandidateActions";
import { KnowledgeGap } from "./KnowledgeItem";
import type { KnowledgeView } from "./view";

const HISTORIC = { state: "historic" as const, flags: [] };

function origin(item: { source_ref: string | null; observed_on: string | null }): string {
  return [item.source_ref, item.observed_on ? formatDate(item.observed_on) : null].filter(Boolean).join(" · ");
}

/**
 * El que l'històric proposa desar, dit a partir de la proposta estructurada i
 * no del seu text: una proposta en negatiu s'ha de llegir en negatiu abans de
 * decidir res.
 */
function Proposed({ observation, vocabulary }: { observation: Observation; vocabulary: Vocabulary | null }) {
  const described = describeProposal(observation, vocabulary);
  if (!described.sentence) return null;
  return (
    <p className="mt-1 text-body text-ink" data-proposed={described.negative ? "negative" : "positive"}>
      <span className="text-muted">{"L'històric indica: "}</span>
      <span className="font-medium">{described.sentence}</span>
    </p>
  );
}

function Notes({ observation, superseded }: { observation: Observation; superseded: boolean }) {
  if (superseded) {
    return (
      <p className="mt-1 text-caption text-muted">
        Ja hi ha una dada actual sobre això, i és la que mana. Aquesta observació només es pot descartar.
      </p>
    );
  }
  if (!isPromotable(observation)) {
    return (
      <p className="mt-1 text-caption text-muted">
        L&apos;històric diu que no es tenia: no es pot confirmar com a actual. Si ara sí que es té, declara-la; si no,
        descarta-ho.
      </p>
    );
  }
  return null;
}

/** Una dada detectada a l'històric, amb el que diu, d'on surt i què s'hi pot fer. */
export function CandidateRow({ observation, view }: { observation: Observation; view: KnowledgeView }) {
  const superseded = view.superseded.has(observation.id);
  const source = origin(observation);
  return (
    <KnowledgeGap
      title={candidateTitle(observation.subject, observation.subject_key, view.vocabulary)}
      state={HISTORIC}
      observationId={observation.id}
      actions={
        <CandidateActions
          observation={observation}
          vocabulary={view.vocabulary}
          canDecide={view.canDecide}
          superseded={superseded}
          onChanged={view.reload}
        />
      }
    >
      <Proposed observation={observation} vocabulary={view.vocabulary} />
      <p className="mt-0.5 text-[13px] leading-5 text-muted">{observation.statement}</p>
      {source && <p className="mt-0.5 text-caption text-muted">{source}</p>}
      <Notes observation={observation} superseded={superseded} />
    </KnowledgeGap>
  );
}

/** El que l'històric diu d'una clau sense dada actual, dins de la seva categoria. */
export function HintList({ hints, view }: { hints: KnowledgeHint[]; view: KnowledgeView }) {
  return (
    <ul className="mt-1.5 space-y-2">
      {hints.map((hint) => {
        const observation = view.observations.get(hint.observation_id);
        const source = origin(hint);
        return (
          <li
            key={hint.observation_id}
            data-observation={hint.observation_id}
            className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between"
          >
            <div className="min-w-0">
              {observation && <Proposed observation={observation} vocabulary={view.vocabulary} />}
              <p className={observation ? "mt-0.5 text-[13px] leading-5 text-muted" : "text-body text-ink"}>{hint.statement}</p>
              {source && <p className="mt-0.5 text-caption text-muted">{source}</p>}
              {observation && <Notes observation={observation} superseded={view.superseded.has(observation.id)} />}
            </div>
            {observation && (
              <div className="flex shrink-0 flex-wrap items-center gap-1">
                <CandidateActions
                  observation={observation}
                  vocabulary={view.vocabulary}
                  canDecide={view.canDecide}
                  superseded={view.superseded.has(observation.id)}
                  onChanged={view.reload}
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
