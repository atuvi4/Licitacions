/**
 * F-06a: el PBL amb IVA segons la PSCP a la fitxa (fet «Pressupost base de licitació amb
 * IVA» i atribució). F-06e: les cel·les VEC, PBL sense IVA i PBL amb IVA de la taula «Lots».
 * Tot visible, no només en un `title` (patró P2-04/P2-06). Els textos i les regles són a
 * `lib/pscpAmounts`.
 */

import { formatAmount } from "@/lib/format";
import {
  type LotAmountCellView,
  PSCP_LINK_TEXT,
  PSCP_LOT_SOURCE_TEXT,
  PSCP_LOT_ZERO_TEXT,
  PSCP_PENDING_TEXT,
  PSCP_ZERO_TEXT,
  pscpConflictNotice,
  pscpLotConflictReason,
  pscpSourceLine,
} from "@/lib/pscpAmounts";
import { AMOUNT_NOT_PUBLISHED, type AmountObservation } from "@/lib/tenders";

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

/**
 * F-06e: una cel·la d'import d'un lot (VEC, PBL sense IVA o PBL amb IVA; la columna diu quin).
 * - PLACSP (> 0): l'import, com sempre.
 * - PSCP `accepted`: l'import « · PSCP», amb l'enllaç a la publicació si n'hi ha.
 * - PSCP `conflict`: «Pendent de verificació…» i el motiu; mai l'import com a valor.
 * - PSCP `no_value` a 0: «Publicat a 0,00 € a la PSCP».
 * - Res: un guió (i «No consta…» per als lectors de pantalla). Cap «Sense import a la PLACSP» a
 *   la cel·la: la taula ho diu una sola vegada.
 */
export function LotAmountValue({ cell, currency }: { cell: LotAmountCellView; currency: string }) {
  if (cell.source === "placsp") {
    return (
      <span className="whitespace-nowrap tabular-nums" data-lot-amount="placsp">
        {formatAmount(cell.value, currency)}
      </span>
    );
  }
  if (cell.source === "none") {
    return (
      <span className="text-muted" data-lot-amount="none">
        <span aria-hidden="true">—</span>
        <span className="sr-only">{AMOUNT_NOT_PUBLISHED}</span>
      </span>
    );
  }
  const observation = cell.observation;
  if (observation.status === "conflict") {
    return (
      <span className="block" data-lot-amount="pscp" data-pscp-lot="conflict">
        <span>{PSCP_PENDING_TEXT}</span>
        <span className="mt-0.5 block text-caption text-muted" role="note" data-pscp-notice>
          {pscpLotConflictReason(observation)}
          <PublicationLink url={observation.publication_url} />
        </span>
      </span>
    );
  }
  if (observation.status === "no_value") {
    return (
      <span className="text-muted" data-lot-amount="pscp" data-pscp-lot="no_value">
        {PSCP_LOT_ZERO_TEXT}
      </span>
    );
  }
  return (
    <span className="whitespace-nowrap tabular-nums" data-lot-amount="pscp" data-pscp-lot="accepted">
      {formatAmount(observation.amount, observation.currency)}
      {" · "}
      {observation.publication_url ? (
        <a
          href={observation.publication_url}
          target="_blank"
          rel="noopener noreferrer"
          className={LINK}
          title="Veure la publicació a la PSCP"
          data-pscp-lot-link
        >
          {PSCP_LOT_SOURCE_TEXT} ↗
        </a>
      ) : (
        PSCP_LOT_SOURCE_TEXT
      )}
    </span>
  );
}

/** F-06e: la indicació única de la taula «Lots» quan algun import és de la PSCP. */
export function LotsPscpNote({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p className="px-3.5 pt-3 text-caption text-muted" data-lots-pscp-note>
      {text}
    </p>
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
