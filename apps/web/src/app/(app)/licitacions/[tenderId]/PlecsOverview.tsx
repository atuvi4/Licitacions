"use client";

/**
 * Peces de la pestanya Plecs que diuen l'estat HONEST del paquet (3B-2 §7.1.5):
 * d'on surt cada document i si l'anàlisi el pot llegir, els àlies plegats sota el
 * canònic, els retirats de la font amb la data, «N de M documents analitzables
 * llegits» i l'avís de paquet incomplet abans de cap quota.
 */

import { StatusPill } from "@/components/ui";
import {
  PACKAGE_COVERAGE_TEXT,
  packageCoverageState,
  type TenderDocumentMeta,
  type TenderPackage,
} from "@/lib/tenders";

import {
  PACKAGE_STATUS_LABELS,
  aliasesOf,
  contributionBadge,
  gapImpact,
  gapText,
  processingLines,
  originBadge,
  readingCount,
  readingLine,
  removedLabel,
} from "./package";

/** Origen, estat de l'aportació i àlies d'un document de la taula. */
export function DocumentBadges({
  document,
  documents,
}: {
  document: TenderDocumentMeta;
  documents: TenderDocumentMeta[];
}) {
  const contribution = contributionBadge(document);
  const aliases = aliasesOf(document, documents);
  const canonical = document.duplicate_of_document_id
    ? documents.find((candidate) => candidate.id === document.duplicate_of_document_id)
    : undefined;
  return (
    <div className="mt-1 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusPill level="neutral" size="sm" outline>
          {originBadge(document)}
        </StatusPill>
        {contribution && (
          <StatusPill level={contribution.level} size="sm">
            {contribution.label}
          </StatusPill>
        )}
        {document.duplicate_of_document_id && (
          <StatusPill level="neutral" size="sm">
            {canonical ? `àlies de ${canonical.name}` : "àlies d'un altre document"}
          </StatusPill>
        )}
      </div>
      {document.contribution_status === "pending_verification" && (
        <p className="text-caption text-muted">Es veu, però no entra a l&apos;anàlisi fins que l&apos;operador el verifiqui.</p>
      )}
      {document.contribution_status === "rejected" && (
        <p className="text-caption text-muted">L&apos;operador l&apos;ha rebutjat: no entra a l&apos;anàlisi.</p>
      )}
      {aliases.length > 0 && (
        <ul className="space-y-0.5">
          {aliases.map((alias) => (
            <li key={alias.id} className="flex flex-wrap items-center gap-1.5 text-caption text-muted">
              <span className="truncate" title={alias.name}>
                {alias.name}
              </span>
              <StatusPill level="neutral" size="sm">
                àlies de {document.name}
              </StatusPill>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** «N de M documents analitzables llegits». */
export function PlecsReadingLine({ documents }: { documents: TenderDocumentMeta[] }) {
  const { read, total } = readingCount(documents);
  return (
    <p className="text-[13px] text-muted">
      <span className="font-medium text-ink">{readingLine(documents)}</span>
      {total > 0 && read < total && " · els que no tenen text llegit no entren a l'anàlisi fins que es processin"}
    </p>
  );
}

/**
 * La línia de cobertura del paquet (A4), sempre: completa, incompleta amb els
 * forats, o sense comprovar. L'avís d'incomplet va abans de cap quota.
 */
export function PackageNotice({ pkg, documents }: { pkg: TenderPackage | null; documents: TenderDocumentMeta[] }) {
  if (!pkg) return null;
  const names = new Map(documents.map((document) => [document.id, document.name]));
  return (
    <div className="space-y-2">
      <PackageCoverage pkg={pkg} names={names} />
      <ProcessingNotice pkg={pkg} names={names} />
    </div>
  );
}

/**
 * Processabilitat, a part de la cobertura: documents que hi són però que Tuvi encara
 * no pot llegir. Mai «paquet incomplet».
 */
export function ProcessingNotice({ pkg, names }: { pkg: TenderPackage; names: Map<string, string> }) {
  const lines = processingLines(pkg.processing, names);
  if (lines.length === 0) return null;
  return (
    <div role="status" className="rounded-card border border-border bg-subtle px-4 py-3 text-[13px]">
      <p className="font-heading font-semibold text-ink">
        Hi ha documents presents que Tuvi encara no pot processar completament
      </p>
      <ul className="mt-1.5 space-y-1 text-ink" aria-label="Documents presents sense processar completament">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="mt-1.5 text-muted">
        No hi falta cap document per això: el que diuen aquests documents no s&apos;avalua mentre no es puguin llegir.
      </p>
    </div>
  );
}

function PackageCoverage({ pkg, names }: { pkg: TenderPackage; names: Map<string, string> }) {
  // La mateixa semàntica que la quota (F10): `packageCoverageState`.
  const state = packageCoverageState(pkg);
  if (state === "complete") {
    return (
      <p role="status" className="text-[13px] text-muted">
        <span className="font-heading font-semibold text-ink">{PACKAGE_STATUS_LABELS.complete}.</span>{" "}
        {PACKAGE_COVERAGE_TEXT.complete}
      </p>
    );
  }
  if (state === "unknown") {
    return (
      <div role="status" className="rounded-card border border-border bg-subtle px-4 py-3 text-[13px] text-muted">
        <span className="font-heading font-semibold text-ink">{PACKAGE_STATUS_LABELS.unknown}.</span>{" "}
        {PACKAGE_COVERAGE_TEXT.unknown} Que no hi hagi cap avís no vol dir que no hi falti res.
      </div>
    );
  }
  return (
    <div role="status" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-[13px]">
      <p className="font-heading font-semibold text-warning">
        {PACKAGE_STATUS_LABELS.incomplete}: {PACKAGE_COVERAGE_TEXT.incomplete}
      </p>
      <ul className="mt-1.5 space-y-1 text-ink">
        {pkg.gaps.map((gap, index) => (
          <li key={`${gap.code}-${gap.key ?? index}`}>
            {gapText(gap, names)} <span className="text-muted">· {gapImpact(gap)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-muted">
        L&apos;anàlisi d&apos;un paquet incomplet només es pot demanar acceptant-ho explícitament: el que diria el
        document que falta no s&apos;avalua.
      </p>
    </div>
  );
}

/**
 * Els documents retirats, amb la data i qui els ha retirat (la font només si eren
 * del feed, M3). Mai desapareixen en silenci.
 */
export function RemovedDocuments({ documents }: { documents: TenderDocumentMeta[] }) {
  if (documents.length === 0) return null;
  return (
    <div className="border-t border-border px-5 py-3">
      <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">Documents retirats</p>
      <ul className="mt-1.5 space-y-1">
        {documents.map((document) => (
          <li key={document.id} className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
            <span className="text-ink">{document.name}</span>
            <span className="text-caption text-muted">{removedLabel(document) ?? "Retirat"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
