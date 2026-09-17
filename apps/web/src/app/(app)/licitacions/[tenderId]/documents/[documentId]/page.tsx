"use client";

/**
 * Text extret d'un plec, pàgina a pàgina.
 *
 * No és un lector de PDF: és la comprovació visual que la pàgina 27 desada és
 * de veritat la pàgina 27 del document. Abans de connectar-hi cap IA, això és
 * el que cal poder verificar a ull. El model d'extracció no canvia aquí: una
 * revisió és immutable i una cita apunta sempre a la seva.
 */

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import {
  Banner,
  Button,
  Card,
  CardHeader,
  ErrorState,
  Input,
  LinkButton,
  LoadingRows,
  PageHeader,
  PageSkeleton,
  StatusPill,
} from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import { DOCUMENT_STATE_LEVEL, QA_LEVEL, formatDateTime } from "@/lib/labels";
import {
  DOCUMENT_STATE_LABELS,
  TENDER_DOCUMENT_LABELS,
  documentState,
  formatBytes,
  type TenderDetail,
  type TenderDocumentFileSummary,
} from "@/lib/tenders";

interface FileRead {
  id: string;
  version_number: number;
  is_current: boolean;
  download_status: string;
  source_url: string;
  original_filename: string | null;
  downloaded_at: string | null;
  error_code: string | null;
  content: { sha256: string; file_size: number; mime_type: string } | null;
  extraction: {
    id: string;
    parser_version: string;
    revision_number: number;
    status: string;
    quality: string;
    page_count: number;
    text_char_count: number;
    ocr_candidate: boolean;
    error_code: string | null;
    /** `null` a les extraccions anteriors a la QA: no s'han avaluat mai. */
    qa_status: "good" | "warning" | "poor" | null;
    qa_issues: { page_number: number | null; code: string }[];
  } | null;
}

/** Etiquetes de la QA del text. Els codis venen del backend i són tancats. */
const QA_LABELS: Record<string, string> = {
  good: "Bona",
  warning: "Avisos",
  poor: "Pobra",
};

const QA_ISSUE_LABELS: Record<string, string> = {
  EMPTY_PAGE: "pàgina sense text",
  IMAGE_ONLY_PAGE: "sense text però amb imatges (possible escaneig)",
  LOW_TEXT_DENSITY: "poc text per al conjunt del document",
  DENSITY_DROP: "molt menys text que la resta de pàgines",
  POSSIBLE_MISSING_FORMULA: "s'anuncia una fórmula que no apareix al text",
  POSSIBLE_MISSING_TABLE: "s'anuncia una taula que no apareix al text",
  POSSIBLE_TRUNCATION: "el text sembla tallat",
  UNREADABLE_CHARACTERS: "caràcters il·legibles (codificació)",
  PARSER_WARNING: "el parser no ha pogut llegir la pàgina",
};

interface PageRead {
  page_number: number;
  text: string;
  char_count: number;
  status: string;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

function DocumentText() {
  const params = useParams<{ tenderId: string; documentId: string }>();
  const search = useSearchParams();
  // Una cita apunta a una revisió CONCRETA. Si arribem des de «Veure font», cal
  // obrir aquella, no la vigent: si el document s'ha reextret, la revisió nova
  // pot dir una altra cosa i la cita deixaria de ser verificable.
  const citedExtraction = search.get("extraction");
  const citedPage = Number(search.get("page")) || null;

  // Les rutes van sense `/api/v1`: el proxy `/bff/*` ja hi reescriu a sobre.
  const files = useResource<FileRead[]>(`/tenders/${params.tenderId}/documents/${params.documentId}/files`);
  const [pageNumber, setPageNumber] = useState(citedPage ?? 1);

  // El títol ha de ser el NOM del document («Plec de prescripcions tècniques»),
  // no el tros final de la seva URL: `original_filename` surt de l'adreça i a
  // PLACSP acaba sent el nom d'un servlet, que no diu res a ningú.
  const { data: tender } = useResource<TenderDetail>(`/tenders/${params.tenderId}`);
  const meta = tender?.documents?.find((document) => document.id === params.documentId) ?? null;
  const documentName = meta?.name ?? null;
  const backHref = `/licitacions/${params.tenderId}?tab=plecs`;

  const citedFile = citedExtraction ? files.data?.find((file) => file.extraction?.id === citedExtraction) : undefined;
  const current = citedFile ?? files.data?.find((file) => file.is_current) ?? files.data?.[0] ?? null;
  const extraction = current?.extraction ?? null;
  const showingCitedRevision = Boolean(citedExtraction && extraction?.id === citedExtraction);

  const page = useResource<PageRead>(
    extraction ? `/tender-document-extractions/${extraction.id}/pages/${pageNumber}` : null,
  );

  const total = extraction?.page_count ?? 0;

  // El resum reutilitza l'etiquetatge de la fitxa perquè els dos llocs han de
  // dir exactament el mateix del mateix document.
  const summary: TenderDocumentFileSummary | null = current
    ? {
        file_id: current.id,
        version_number: current.version_number,
        download_status: current.download_status as never,
        file_size: current.content?.file_size ?? null,
        sha256: current.content?.sha256 ?? null,
        extraction_status: (extraction?.status ?? null) as never,
        quality: (extraction?.quality ?? null) as never,
        page_count: extraction?.page_count ?? null,
        text_char_count: extraction?.text_char_count ?? null,
        ocr_candidate: extraction?.ocr_candidate ?? false,
        extraction_id: extraction?.id ?? null,
        processed_at: current.downloaded_at,
        error_code: current.error_code ?? extraction?.error_code ?? null,
      }
    : null;

  const breadcrumb = (
    <p className="mb-3 text-[13px] text-muted">
      <Link href="/licitacions" className="text-brand-strong hover:text-accent-800">
        Oportunitats
      </Link>
      {" › "}
      <Link href={backHref} className="text-brand-strong hover:text-accent-800">
        {tender?.title ?? "Licitació"}
      </Link>
      {" › "}
      {documentName ?? "Document"}
    </p>
  );

  if (files.error && !files.data) {
    return (
      <>
        {breadcrumb}
        <ErrorState
          title="No hem pogut carregar el document"
          description="La connexió amb el servei ha fallat. El text extret continua desat: torna-ho a provar."
          action={
            <Button variant="primary" onClick={() => void files.reload()}>
              Torna-ho a provar
            </Button>
          }
          technical={{ method: "GET /files", code: files.error }}
        />
      </>
    );
  }
  if (!files.data) {
    return <PageSkeleton variant="detail" title={documentName ?? undefined} context="carregant el text extret…" />;
  }
  if (!current) {
    return (
      <>
        {breadcrumb}
        <Card>
          <EmptyState
            title="Aquest document encara no s'ha processat"
            description="Processa'l des de la pestanya Plecs per descarregar-ne l'original i extreure'n el text."
            action={
              <LinkButton href={backHref} variant="primary" size="md">
                Torna als plecs
              </LinkButton>
            }
          />
        </Card>
      </>
    );
  }

  const state = documentState(summary);
  const issues = extraction?.qa_issues ?? [];
  const readingWarnings = issues.length > 0 || Boolean(extraction?.ocr_candidate);
  const originalHref = `/bff/tender-document-files/${current.id}/original`;

  return (
    <div className="space-y-4">
      {breadcrumb}
      <PageHeader
        title={documentName ?? current.original_filename ?? "Document"}
        description={
          meta
            ? [TENDER_DOCUMENT_LABELS[meta.document_type], meta.published_at ? `publicat el ${formatDate(meta.published_at)}` : null]
                .filter(Boolean)
                .join(" · ")
            : "Nom no publicat per la font."
        }
        actions={
          // Un <a> normal: és una descàrrega del BFF, no una pàgina; un next/link la prefetejaria.
          <a
            href={originalHref}
            target="_blank"
            rel="noopener noreferrer"
            className="ui-action-md inline-flex items-center justify-center border border-border-strong px-3 text-[13.5px] font-semibold text-ink hover:bg-subtle"
          >
            Obre l&apos;original ↗
          </a>
        }
      />

      {readingWarnings && (
        <Banner
          level="attention"
          kicker="Lectura"
          action={
            <a href={originalHref} target="_blank" rel="noopener noreferrer" className="text-[13px] text-brand-strong hover:text-accent-800">
              Compara amb el PDF ↗
            </a>
          }
        >
          {extraction?.ocr_candidate && (
            <span>
              S&apos;ha extret molt poc text: podria ser un document escanejat. No s&apos;hi ha aplicat cap OCR.{" "}
            </span>
          )}
          {issues.length > 0 && (
            <span>
              En aquestes pàgines podria haver-hi informació que al PDF es veu i al text no hi és:{" "}
              {issues
                .map((issue) => `${issue.page_number ? `Pàgina ${issue.page_number}` : "Document"}: ${QA_ISSUE_LABELS[issue.code] ?? "senyal desconegut"}`)
                .join(" · ")}
              .
            </span>
          )}
        </Banner>
      )}

      {showingCitedRevision && !current.is_current && (
        <p className="border border-border-strong bg-subtle px-3.5 py-2.5 text-[13px] text-muted">
          Estàs veient la revisió {extraction?.revision_number} perquè és la que va originar la cita. Ja no és la
          revisió vigent del document.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader
              title="Text extret"
              description={total ? `Pàgina ${pageNumber} de ${total}` : "Sense pàgines"}
            />
            {total === 0 ? (
              <p className="px-5 py-4 text-[13px] text-muted">Aquest document no té text extret.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
                  <Button variant="secondary" size="sm" onClick={() => setPageNumber((n) => Math.max(1, n - 1))} disabled={pageNumber <= 1}>
                    Anterior
                  </Button>
                  <Input
                    type="number"
                    min={1}
                    max={total}
                    value={pageNumber}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (value >= 1 && value <= total) setPageNumber(value);
                    }}
                    className="w-20"
                    aria-label="Número de pàgina"
                  />
                  <Button variant="secondary" size="sm" onClick={() => setPageNumber((n) => Math.min(total, n + 1))} disabled={pageNumber >= total}>
                    Següent
                  </Button>
                </div>
                {page.loading && !page.data ? (
                  <LoadingRows />
                ) : (
                  <pre className="max-h-[60vh] overflow-auto px-5 py-4 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-ink">
                    {page.data?.text || "(pàgina sense text extret)"}
                  </pre>
                )}
              </>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader title={showingCitedRevision ? "Revisió citada" : "Versió vigent"} />
            <dl className="grid grid-cols-2 gap-3 px-5 py-4 text-[13px]">
              <Fact label="Estat">
                <StatusPill level={DOCUMENT_STATE_LEVEL[state]} size="sm">
                  {DOCUMENT_STATE_LABELS[state]}
                </StatusPill>
              </Fact>
              <Fact label="Versió">
                v{current.version_number} de {files.data.length}
              </Fact>
              <Fact label="Mida">{formatBytes(current.content?.file_size ?? null)}</Fact>
              <Fact label="Pàgines">{total || "—"}</Fact>
              <Fact label="Qualitat de l'extracció">
                {extraction?.qa_status ? (
                  <StatusPill level={QA_LEVEL[extraction.qa_status]} size="sm">
                    {QA_LABELS[extraction.qa_status]}
                  </StatusPill>
                ) : (
                  <span className="text-muted">no avaluada</span>
                )}
              </Fact>
              <Fact label="Parser">
                {extraction ? `${extraction.parser_version} · revisió ${extraction.revision_number}` : "—"}
              </Fact>
              <div className="col-span-2 min-w-0">
                <dt className="text-caption text-muted">SHA-256</dt>
                <dd className="mt-0.5 truncate font-mono text-[11px] text-muted" title={current.content?.sha256 ?? undefined}>
                  {current.content?.sha256 ?? "—"}
                </dd>
              </div>
            </dl>
          </Card>

          <Card>
            <CardHeader
              title="Versions"
              description="Cada descàrrega del document és una versió; cada versió té la seva extracció, immutable."
            />
            <ul className="divide-y divide-border">
              {files.data.map((file) => (
                <li key={file.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-[13px]">
                  <span>
                    <span className="font-medium text-ink">v{file.version_number}</span>
                    {file.downloaded_at && <span className="text-muted"> · {formatDateTime(file.downloaded_at)}</span>}
                    {file.is_current && <span className="text-muted"> · vigent</span>}
                    {file.extraction && <span className="text-muted"> · revisió {file.extraction.revision_number}</span>}
                  </span>
                  {file.extraction && file.id !== current.id && (
                    <Link
                      href={`/licitacions/${params.tenderId}/documents/${params.documentId}?extraction=${file.extraction.id}`}
                      className="text-brand-strong hover:text-accent-800"
                    >
                      Veure
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function DocumentTextPage() {
  return (
    <Suspense fallback={<PageSkeleton variant="detail" context="carregant el text extret…" />}>
      <DocumentText />
    </Suspense>
  );
}
