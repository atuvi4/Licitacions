"use client";

import { Button, Card, CardHeader, EmptyState } from "@/components/ui";
import { groupCandidates } from "@/lib/knowledge";

import { CandidateRow } from "./CandidateRow";
import type { KnowledgeView } from "./view";

/**
 * «Històric detectat»: el que diuen les licitacions passades, per categoria.
 * Només informa. Una dada d'aquí no satisfà mai cap requisit fins que una
 * persona la confirma com a actual.
 */
export function HistoricTab({ view, onShowCurrent }: { view: KnowledgeView; onShowCurrent: () => void }) {
  const candidates = [...view.observations.values()];
  if (candidates.length === 0) {
    return (
      <Card>
        <EmptyState
          title="Cap dada detectada a l'històric"
          description="No hi ha suggeriments de licitacions passades per revisar. El que l'empresa és i pot fer es pot declarar directament."
          action={
            <Button variant="primary" size="md" onClick={onShowCurrent}>
              Declara coneixement actual
            </Button>
          }
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {groupCandidates(candidates, view.superseded).map((group) => (
        <Card key={group.key}>
          <CardHeader title={group.label} description={`${group.items.length} per revisar, detectats a l'històric.`} />
          <ul>
            {group.items.map((observation) => (
              <CandidateRow key={observation.id} observation={observation} view={view} />
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}
