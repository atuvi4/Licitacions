import { ScoreValue } from "@/components/PreFit";
import { StatusPill } from "@/components/ui";
import { MATCH_STATUS_LABELS, shortSummary, type TenderMatch } from "@/lib/matching";

/**
 * El pre-fit com a dada, no com a veredicte: sense càlcul és «Sense calcular»
 * (neutre, mai vermell ni zero) i amb càlcul la xifra va sempre amb el motiu.
 */
export function PreFitSummary({ match }: { match: TenderMatch | null }) {
  if (!match) return <span className="text-caption text-muted">Pre-fit: Sense calcular</span>;
  return (
    <div className="flex flex-col items-start gap-1 md:items-end">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-caption text-muted">Pre-fit</span>
        <ScoreValue score={match.score} size="sm" />
        {match.is_stale && (
          <StatusPill level="attention" size="sm">
            Desactualitzat
          </StatusPill>
        )}
      </div>
      <p className="text-caption text-muted">{shortSummary(match)}</p>
      {match.status !== "candidate" && <p className="text-caption text-faint">{MATCH_STATUS_LABELS[match.status]}</p>}
    </div>
  );
}
