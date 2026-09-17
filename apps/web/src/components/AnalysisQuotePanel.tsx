"use client";

/**
 * Bloc de confirmació d'una anàlisi: el volum abans de fer despesa.
 *
 * Mostra el que el backend ha desat com a preview (documents, pàgines, chunks,
 * perfil i model, estimació de tokens si n'hi ha). No hi ha cap import en
 * diners: encara no hi ha billing. Si el preview ja no val, el backend en torna
 * un de nou i el bloc el substitueix: l'usuari l'ha de tornar a confirmar.
 * Mai un botó que analitzi directament: sempre preview → confirmació humana.
 */

import { Button, PermissionNote } from "@/components/ui";
import type { AnalysisQuote } from "@/lib/jobs";
import { roleLabel } from "@/lib/labels";

const SKIPPED_REASONS: Record<string, string> = {
  document_not_processed: "no processat",
  document_not_analyzable: "sense text analitzable",
  document_without_text: "sense text",
};

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] uppercase";

function formatTokens(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ca-ES");
}

export function AnalysisQuotePanel({
  quote,
  notice,
  canConfirm,
  confirmRoles,
  confirming,
  onConfirm,
  onCancel,
}: {
  quote: AnalysisQuote;
  notice: string | null;
  canConfirm: boolean;
  /** Rols que sí que poden confirmar (`analysis-limits.confirm_roles`). */
  confirmRoles?: string[];
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const reused = quote.reusable_run_id !== null && quote.chunks_to_send === 0;
  const blocked = !quote.allowed;
  const who = confirmRoles && confirmRoles.length > 0 ? confirmRoles.map(roleLabel).join(" o ") : "qui té permís de confirmar";

  const confirmButton = (
    <Button variant="primary" onClick={onConfirm} disabled={confirming || blocked || !canConfirm} loading={confirming}>
      Confirma i analitza
    </Button>
  );

  return (
    <div className="border-b border-border-strong bg-surface px-5 py-4 text-[13px]">
      <p className={`${KICKER} text-brand-strong`}>Abans d&apos;analitzar</p>
      <p className="mt-1 font-heading text-[17px] font-semibold text-ink">Aquest és el volum</p>
      {notice && <p className="mt-1.5 text-warning">{notice}</p>}

      <ul className="mt-3 divide-y divide-border border-t border-b border-border">
        {quote.documents.map((document) => (
          <li key={document.tender_document_id} className="flex justify-between gap-4 py-1.5">
            <span className="truncate">{document.name}</span>
            <span className="shrink-0 text-muted tabular-nums">{document.pages} pàgines</span>
          </li>
        ))}
        {quote.skipped_documents.map((document) => (
          <li key={document.tender_document_id} className="flex justify-between gap-4 py-1.5 text-muted">
            <span className="truncate">{document.name}</span>
            <span className="shrink-0">fora · {SKIPPED_REASONS[document.reason] ?? "no analitzable"}</span>
          </li>
        ))}
      </ul>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
        <div>
          <dt className={`${KICKER} text-muted`}>Pàgines</dt>
          <dd className="tabular-nums">
            {quote.pages_in_plan} de {quote.pages_total}
          </dd>
        </div>
        <div>
          <dt className={`${KICKER} text-muted`}>Chunks a enviar</dt>
          <dd className="tabular-nums">
            {quote.chunks_to_send}
            {quote.chunks_to_send !== quote.chunks ? ` (de ${quote.chunks})` : ""} · màxim {quote.max_chunks}
          </dd>
        </div>
        <div>
          <dt className={`${KICKER} text-muted`}>Estimació de tokens</dt>
          <dd className="tabular-nums">
            {quote.estimated_input_tokens === null
              ? "Sense històric per estimar"
              : `~${formatTokens(quote.estimated_input_tokens)} entrada · ~${formatTokens(quote.estimated_output_tokens)} sortida`}
          </dd>
        </div>
        <div>
          <dt className={`${KICKER} text-muted`}>Perfil i model</dt>
          <dd className="truncate">
            {quote.profile} · {quote.model}
          </dd>
        </div>
      </dl>

      {quote.pages_truncated && (
        <p className="mt-2 text-warning">
          El plec supera el màxim de pàgines per anàlisi: només se n&apos;analitzaran {quote.pages_in_plan}.
        </p>
      )}
      {reused && (
        <p className="mt-2 text-muted">Ja hi ha una anàlisi vigent d&apos;aquests plecs: no es tornarà a executar.</p>
      )}
      {blocked && (
        <p className="mt-2 text-danger">
          Aquesta anàlisi supera el límit del pilot ({quote.max_chunks} chunks) i no es pot llançar.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-start gap-2">
        {canConfirm ? (
          confirmButton
        ) : (
          <PermissionNote who={who} what="confirmar l'anàlisi" action={<span className="text-muted">Pots veure el volum; demana-li que la confirmi.</span>}>
            {confirmButton}
          </PermissionNote>
        )}
        <Button variant="ghost" onClick={onCancel} disabled={confirming}>
          Cancel·la
        </Button>
      </div>
    </div>
  );
}
