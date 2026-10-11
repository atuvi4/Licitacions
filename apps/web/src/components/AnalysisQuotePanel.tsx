"use client";

/**
 * Bloc de confirmació d'una anàlisi: el volum i les condicions abans de fer despesa.
 *
 * Mostra el que el backend ha desat com a preview (documents, pàgines, chunks,
 * perfil i model, estimació de tokens) i, des de 3B-2 (§6.1), sobre què es faria
 * (`provider_basis`), el cost estimat (un sostre, només si hi ha preu), els
 * prerequisits que falten amb text humà i les acceptacions explícites que alguns
 * demanen (paquet incomplet, qualitat del text). Si el preview ja no val, el
 * backend en torna un de nou i el bloc el substitueix: l'usuari l'ha de tornar a
 * confirmar. Mai un botó que analitzi directament: sempre preview → confirmació
 * humana, i «Inicia l'anàlisi» desactivat mentre el servidor digui `allowed=false`.
 */

import { gapImpact, gapText } from "@/app/(app)/licitacions/[tenderId]/package";
import { Button, Checkbox } from "@/components/ui";
import {
  PROVIDER_BASIS_TEXT,
  QUOTE_WARNING_TEXT,
  blockingReasonText,
  type AnalysisQuote,
  type QuoteAcceptance,
  type QuotePackageIssue,
} from "@/lib/jobs";
import {
  PACKAGE_COVERAGE_TEXT,
  packageCoverageState,
  type PackageGapCode,
  type TenderPackage,
} from "@/lib/tenders";

const SKIPPED_REASONS: Record<string, string> = {
  document_not_processed: "no processat",
  document_not_analyzable: "sense text analitzable",
  document_without_text: "sense text",
};

/** Motius de paquet incomplet de la quota (`prerequisites.package.issues`) que parlen d'un document. */
const ISSUE_REASONS: Record<string, string> = {
  document_not_processed: "sense processar",
  document_without_text: "sense text",
  document_not_analyzable: "sense text analitzable",
  selected_document_skipped: "fora de l'anàlisi",
  default_document_skipped: "fora de l'anàlisi",
  duplicate_content: "contingut duplicat",
  classification_not_current: "classificació no vigent",
};

/**
 * Què falta al paquet, en humà, per a la casella d'acceptar-lo (M4): els forats de
 * la fitxa (`gaps` amb `would_provide` o «impacte desconegut») i, si no hi són,
 * els motius de la quota. Cap codi arriba al text.
 */
function packageIssueLines(
  pkg: TenderPackage | null | undefined,
  issues: QuotePackageIssue[],
  names: Map<string, string>,
): string[] {
  if (pkg && pkg.status === "incomplete" && pkg.gaps.length > 0) {
    return pkg.gaps.map((gap) => `${gapText(gap, names)} · ${gapImpact(gap)}`);
  }
  return issues.map((issue) => {
    if (issue.code === "package_gap" && issue.gap_code) {
      const gap = { code: issue.gap_code as PackageGapCode, key: null, would_provide: [] };
      return `${gapText(gap, names)} · ${gapImpact(gap)}`;
    }
    const ids = issue.tender_document_ids ?? (issue.tender_document_id ? [issue.tender_document_id] : []);
    const documents = ids.map((id) => names.get(id) ?? "Un document");
    const reason = ISSUE_REASONS[issue.code] ?? "falta part del paquet documental";
    return documents.length > 0 ? `${documents.join(", ")}: ${reason}` : `Falta part del paquet documental: ${reason}`;
  });
}

/** Per què un document present no es pot processar completament (`prerequisites.processing.issues`). */
const PROCESSING_REASONS: Record<string, string> = {
  document_not_processed: "encara no s'ha processat",
  document_not_analyzable: "en un format o estat que Tuvi encara no pot llegir",
  document_without_text: "sense text llegible",
  no_text: "sense text llegible per a l'anàlisi",
  format_not_analyzable: "en un format que Tuvi encara no pot llegir",
  duplicate_content: "contingut repetit en un altre document",
  duplicate_bytes: "contingut repetit en un altre document",
  duplicate_text: "contingut repetit en un altre document",
};

/**
 * Els documents presents que Tuvi encara no pot processar completament, un per línia
 * amb els seus motius. Mai diu que hi falti cap document.
 */
function processingIssueLines(issues: QuotePackageIssue[], names: Map<string, string>): string[] {
  const reasons = new Map<string, string[]>();
  for (const issue of issues) {
    const key = issue.code === "package_gap" ? issue.gap_code ?? "" : issue.reason ?? issue.code;
    const reason = PROCESSING_REASONS[key] ?? "no es pot processar completament";
    const ids = issue.tender_document_ids ?? (issue.tender_document_id ? [issue.tender_document_id] : []);
    for (const name of ids.length > 0 ? ids.map((id) => names.get(id) ?? "Un document") : ["Un document"]) {
      const list = reasons.get(name) ?? [];
      if (!list.includes(reason)) list.push(reason);
      reasons.set(name, list);
    }
  }
  return [...reasons.entries()].map(([name, list]) => `${name}: ${list.join(" · ")}`);
}

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] uppercase";

const USD = new Intl.NumberFormat("ca-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function formatTokens(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ca-ES");
}

export function AnalysisQuotePanel({
  quote,
  pkg,
  documents,
  notice,
  canConfirm,
  rerun,
  confirming,
  onConfirm,
  onCancel,
  onAccept,
  accepting = false,
}: {
  quote: AnalysisQuote;
  /** El paquet de la fitxa (estat i forats), per dir què falta sota la casella (M4). */
  pkg?: TenderPackage | null;
  /** Els documents coneguts (id → nom), per anomenar els forats d'un document. */
  documents?: Array<{ id: string; name: string }>;
  notice: string | null;
  canConfirm: boolean;
  /** Ja hi ha una anàlisi: aquesta la tornaria a fer. */
  rerun: boolean;
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** Torna a demanar el volum amb una acceptació explícita (forma part de la quota). */
  onAccept?: (acceptance: QuoteAcceptance) => void;
  accepting?: boolean;
}) {
  const reused = quote.reusable_run_id !== null && quote.chunks_to_send === 0;
  const blocked = !quote.allowed;
  const prerequisites = quote.prerequisites ?? null;
  const failed = prerequisites?.failed ?? [];
  const advisory = prerequisites?.advisory ?? [];
  const warnings = quote.warnings ?? prerequisites?.warnings ?? [];
  const basis = quote.provider_basis ?? prerequisites?.provider_basis ?? null;
  const packageAccepted = Boolean(quote.accept_incomplete_package || prerequisites?.package?.accepted);
  const qualityAcknowledged = Boolean(prerequisites?.acknowledge_quality || prerequisites?.quality?.acknowledged);
  const processingAcknowledged = Boolean(
    prerequisites?.acknowledge_processing || prerequisites?.processing?.acknowledged,
  );
  const askPackage = failed.includes("package_incomplete") || packageAccepted;
  const askProcessing = failed.includes("processing_incomplete") || processingAcknowledged;
  const askQuality = failed.includes("quality_warning") || qualityAcknowledged;
  const names = new Map<string, string>([
    ...(documents ?? []).map((document) => [document.id, document.name] as const),
    ...quote.documents.map((document) => [document.tender_document_id, document.name] as const),
    ...quote.skipped_documents.map((document) => [document.tender_document_id, document.name] as const),
  ]);
  const packageIssues = prerequisites?.package?.issues ?? [];
  // La mateixa semàntica que la pestanya Plecs (F10): sense forats explícits, el
  // paquet és «sense verificar» i mai es diu que hi falten documents.
  const packageState = packageCoverageState(
    pkg,
    packageIssues.map((issue) => issue.code),
  );
  const packageLines =
    askPackage && packageState === "incomplete"
      ? packageIssueLines(
          pkg,
          packageIssues.filter((issue) => issue.code !== "classification_not_current"),
          names,
        )
      : [];
  const processingLinesList = askProcessing
    ? processingIssueLines(prerequisites?.processing?.issues ?? [], names)
    : [];
  const reasonText = (code: string | null | undefined) => blockingReasonText(code, packageState);
  // Els altres motius, a part del principal (que ja surt en gran).
  const otherFailures = failed.filter((code) => code !== quote.blocking_reason);
  // El que fa l'acció, abans del perquè no es pot: no és una acció de
  // revisió, és una extracció automàtica nova (amb cost de model).
  const whatItDoes = rerun
    ? "Torna a extreure automàticament els requisits i els criteris dels plecs. La revisió de l'empresa no canvia."
    : "Extreu automàticament els requisits i els criteris dels plecs.";

  return (
    <div className="border-b border-border-strong bg-surface px-5 py-4 text-[13px]">
      <p className={`${KICKER} text-brand-strong`}>{rerun ? "Nova anàlisi del plec" : "Anàlisi del plec"}</p>
      <p className="mt-1 font-heading text-[17px] font-semibold text-ink">Aquest és el volum</p>
      <p className="mt-1 text-muted">{whatItDoes}</p>
      {notice && <p className="mt-1.5 text-warning">{notice}</p>}
      {basis && (
        <p className={`mt-1.5 ${basis === "real" ? "text-ink" : "text-warning"}`}>
          {PROVIDER_BASIS_TEXT[basis] ?? "Base de l'anàlisi desconeguda"}
          {basis === "real" && ` · model ${quote.model}`}
        </p>
      )}

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

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-5">
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
          <dt className={`${KICKER} text-muted`}>Cost estimat</dt>
          <dd className="tabular-nums">
            {quote.estimated_cost_usd === null || quote.estimated_cost_usd === undefined
              ? "sense preu conegut"
              : `fins a ${USD.format(quote.estimated_cost_usd)} $`}
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
      {warnings.map((code) => (
        <p key={code} className="mt-2 text-warning">
          {QUOTE_WARNING_TEXT[code] ?? "Hi ha un avís sobre aquesta anàlisi."}
        </p>
      ))}

      {blocked && (
        <div role="alert" className="mt-3 border border-danger/25 bg-danger-soft px-3 py-2">
          <p className="font-semibold text-danger">No es pot iniciar: {reasonText(quote.blocking_reason)}</p>
          {otherFailures.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink">
              {otherFailures.map((code) => (
                <li key={code}>{reasonText(code)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {advisory.length > 0 && (
        // Amb una simulació o un resultat reaprofitat no hi ha despesa: aquests no
        // bloquegen, però es diuen igualment.
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-muted">
          {advisory.map((code) => (
            <li key={code}>{reasonText(code)}</li>
          ))}
        </ul>
      )}

      {(askPackage || askProcessing || askQuality) && (
        <div className="mt-3 space-y-1.5">
          {askPackage && (
            <div>
              <Checkbox
                label={
                  packageState === "incomplete"
                    ? "Accepto analitzar un paquet incomplet: el que diria el document que falta no s'avalua."
                    : "Accepto analitzar un paquet sense verificar: si hi falta algun document, el que diria no s'avalua."
                }
                checked={packageAccepted}
                disabled={accepting || confirming || !onAccept}
                onChange={(event) => onAccept?.({ accept_incomplete_package: event.currentTarget.checked })}
              />
              {packageState === "unknown" && <p className="mt-1 pl-9 text-muted">{PACKAGE_COVERAGE_TEXT.unknown}</p>}
              {packageLines.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-9 text-muted" aria-label="Què falta al paquet">
                  {packageLines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {askProcessing && (
            <div>
              <Checkbox
                label="Accepto que hi ha documents presents que Tuvi encara no pot processar completament: el que diuen no s'avalua."
                checked={processingAcknowledged}
                disabled={accepting || confirming || !onAccept}
                onChange={(event) => onAccept?.({ acknowledge_processing: event.currentTarget.checked })}
              />
              {processingLinesList.length > 0 && (
                <ul
                  className="mt-1 list-disc space-y-0.5 pl-9 text-muted"
                  aria-label="Documents presents que no es poden processar completament"
                >
                  {processingLinesList.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {askQuality && (
            <Checkbox
              label="Accepto que el text té avisos de qualitat i que l'extracció en pot quedar afectada."
              checked={qualityAcknowledged}
              disabled={accepting || confirming || !onAccept}
              onChange={(event) => onAccept?.({ acknowledge_quality: event.currentTarget.checked })}
            />
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-start gap-2">
        <Button
          variant="primary"
          onClick={onConfirm}
          disabled={confirming || accepting || blocked || !canConfirm}
          loading={confirming}
        >
          Inicia l&apos;anàlisi
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={confirming}>
          Cancel·la
        </Button>
      </div>
      {!canConfirm && (
        // El permís no canvia (el backend el torna a comprovar): només s'explica
        // sense el vocabulari intern de rols.
        <p className="mt-2 text-[13px] leading-5 text-warning">
          Només els responsables de la licitació poden iniciar una anàlisi nova, perquè té cost. Pots consultar-ne el
          volum i demanar-los que la iniciïn.
        </p>
      )}
    </div>
  );
}
