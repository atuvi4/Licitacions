/**
 * F-06a: el PBL amb IVA segons la PSCP a la fitxa (fet «Pressupost base de licitació amb
 * IVA», línia de lot i atribució). Tot visible, no només en un `title` (patró P2-04/P2-06).
 * Els textos i les regles són a `lib/pscpAmounts`.
 */

import { formatAmount } from "@/lib/format";
import {
  PSCP_LINK_TEXT,
  PSCP_PENDING_TEXT,
  PSCP_ZERO_TEXT,
  pscpConflictNotice,
  pscpLotLine,
  pscpSourceLine,
} from "@/lib/pscpAmounts";
import type { AmountObservation } from "@/lib/tenders";

const LINK = "text-brand-strong hover:text-accent-800";

function PublicationLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <>
      {" · "}
      <a href={url} target="_blank" rel="noopener noreferrer" className={LINK}>
        {PSCP_LINK_TEXT}
      </a>
    </>
  );
}

function SourceLine({ observation }: { observation: AmountObservation }) {
  return (
    <p className="mt-0.5 text-caption text-muted" data-pscp-source>
      {pscpSourceLine(observation.source_updated_at)}
      <PublicationLink url={observation.publication_url} />
    </p>
  );
}

/** El valor del fet quan la PSCP en té una observació que es mostra. */
export function PscpBudgetValue({ observation }: { observation: AmountObservation }) {
  if (observation.status === "conflict") {
    return (
      <div data-pscp-status="conflict">
        <span>{PSCP_PENDING_TEXT}</span>
        <p className="mt-1 border-l-2 border-border-strong pl-2 text-caption text-muted" role="note" data-pscp-notice>
          {pscpConflictNotice(observation)}
          <PublicationLink url={observation.publication_url} />
        </p>
      </div>
    );
  }
  if (observation.status === "no_value") {
    return (
      <div data-pscp-status="no_value">
        <span>{PSCP_ZERO_TEXT}</span>
        <SourceLine observation={observation} />
      </div>
    );
  }
  return (
    <div data-pscp-status="accepted">
      <span className="tabular-nums">{formatAmount(observation.amount, observation.currency)}</span>
      <SourceLine observation={observation} />
    </div>
  );
}

/** La línia de la PSCP sota l'import d'un lot. */
export function PscpLotLine({ observation }: { observation: AmountObservation | undefined }) {
  const text = pscpLotLine(observation);
  if (!text) return null;
  return (
    <span className="block text-caption text-muted tabular-nums" data-pscp-lot={observation?.status}>
      {text}
    </span>
  );
}

export function PscpAttribution({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p className="text-caption text-muted sm:col-span-3" data-pscp-attribution>
      {text}
    </p>
  );
}
