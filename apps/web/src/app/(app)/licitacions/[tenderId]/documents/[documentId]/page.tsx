"use client";

/**
 * Text extret d'un plec, pàgina a pàgina.
 *
 * No és un lector de PDF: és la comprovació visual que la pàgina 27 desada és
 * de veritat la pàgina 27 del document. Abans de connectar-hi cap IA, això és
 * el que cal poder verificar a ull.
 */

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Input,
  LoadingRows,
  PageHeader,
} from "@/components/ui";
import { useResource } from "@/lib/hooks";
import {
  DOCUMENT_STATE_LABELS,
  DOCUMENT_STATE_TONES,
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

/** Etiquetes de la QA del text. Els codis venen del backend i son tancats. */
const QA_LABELS: Record<string, string> = {
  good: "Bona",
  warning: "Avisos",
  poor: "Pobra",
};

const QA_TONES: Record<string, "success" | "warning" | "danger"> = {
  good: "success",
  warning: "warning",
  poor: "danger",
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

export default function DocumentTextPage() {
  const params = useParams<{ tenderId: string; documentId: string }>();
  const search = useSearchParams();
  // Una cita apunta a una revisió CONCRETA. Si arribem des de "Veure font", cal
  // obrir aquella, no la vigent: si el document s'ha reextret, la revisió nova
  // pot dir una altra cosa i la cita deixaria de ser verificable.
  const citedExtraction = search.get("extraction");
  const citedPage = Number(search.get("page")) || null;

  // Les rutes van sense `/api/v1`: el proxy `/bff/*` ja hi reescriu a sobre.
  const {
    data: files,
    loading,
    error,
  } = useResource<FileRead[]>(
    `/tenders/${params.tenderId}/documents/${params.documentId}/files`,
  );
  const [pageNumber, setPageNumber] = useState(citedPage ?? 1);

  // El títol ha de ser el NOM del document ("Plec de prescripcions tècniques"),
  // no el tros final de la seva URL: `original_filename` surt de l'adreça i a
  // PLACSP acaba sent el nom d'un servlet ("GetDocumentByIdServlet"), que no
  // diu res a ningú. L'adreça segueix visible a la zona tècnica, que és on
  // toca.
  const { data: tender } = useResource<TenderDetail>(`/tenders/${params.tenderId}`);
  const documentName =
    tender?.documents?.find((document) => document.id === params.documentId)?.name ?? null;

  const citedFile = citedExtraction
    ? files?.find((file) => file.extraction?.id === citedExtraction)
    : undefined;
  const current = citedFile ?? files?.find((file) => file.is_current) ?? files?.[0] ?? null;
  const extraction = current?.extraction ?? null;
  const showingCitedRevision = Boolean(citedExtraction && extraction?.id === citedExtraction);

  const { data: page, loading: pageLoading } = useResource<PageRead>(
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

  if (loading) return <LoadingRows />;
  if (error) return <ErrorNotice message={error} />;
  if (!current) return <EmptyState title="Aquest document encara no s'ha processat" />;

  const state = documentState(summary);

  return (
    <div className="space-y-4">
      <Link
        href={`/licitacions/${params.tenderId}`}
        className="text-[13px] text-brand hover:underline"
      >
        ← Torna a la licitació
      </Link>
      <PageHeader
        title={documentName ?? current.original_filename ?? "Document"}
        description={documentName ? undefined : "Nom no publicat per la font."}
      />

      <Card>
        <CardHeader title="Versió actual" />
        <dl className="grid grid-cols-2 gap-3 px-5 py-4 text-[13px] md:grid-cols-4">
          <div>
            <dt className="text-muted">Estat</dt>
            <dd>
              <Badge tone={DOCUMENT_STATE_TONES[state]}>{DOCUMENT_STATE_LABELS[state]}</Badge>
            </dd>
          </div>
          <div>
            <dt className="text-muted">Versió</dt>
            <dd>
              v{current.version_number} de {files?.length ?? 1}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Mida</dt>
            <dd>{formatBytes(current.content?.file_size ?? null)}</dd>
          </div>
          <div>
            <dt className="text-muted">Pàgines</dt>
            <dd>{total || "—"}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-muted">SHA-256</dt>
            <dd className="truncate font-mono text-[11px]">{current.content?.sha256 ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Qualitat de l&apos;extracció</dt>
            <dd>
              {extraction?.qa_status ? (
                <Badge tone={QA_TONES[extraction.qa_status]}>
                  {QA_LABELS[extraction.qa_status]}
                </Badge>
              ) : (
                <span className="text-muted">no avaluada</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Parser</dt>
            <dd>
              {extraction
                ? `${extraction.parser_version} · revisió ${extraction.revision_number}`
                : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Original</dt>
            <dd>
              <a
                href={`/bff/tender-document-files/${current.id}/original`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand hover:underline"
              >
                Obre ↗
              </a>
            </dd>
          </div>
        </dl>
        {extraction && extraction.qa_issues.length > 0 && (
          <div className="border-t border-border px-5 py-3">
            <p className="text-[12px] font-medium">
              Senyals que el text extret pot ser incomplet
            </p>
            <p className="mt-0.5 text-[12px] text-muted">
              No vol dir que el document estigui malament: vol dir que en aquestes pàgines
              podria haver-hi informació que al PDF es veu i al text no hi és.
            </p>
            <ul className="mt-2 space-y-1 text-[12px]">
              {extraction.qa_issues.map((issue, index) => (
                <li key={`${issue.code}-${issue.page_number ?? "doc"}-${index}`}>
                  <span className="text-muted">
                    {issue.page_number ? `Pàgina ${issue.page_number}` : "Document"}:
                  </span>{" "}
                  {QA_ISSUE_LABELS[issue.code] ?? issue.code}
                </li>
              ))}
            </ul>
          </div>
        )}
        {showingCitedRevision && !current.is_current && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            Estàs veient la revisió {extraction?.revision_number} perquè és la que va originar la
            cita. Ja no és la revisió vigent del document.
          </p>
        )}
        {extraction?.ocr_candidate && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            S&apos;ha extret molt poc text. Podria ser un document escanejat. No s&apos;hi ha
            aplicat cap OCR.
          </p>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Text extret"
          description={`Pàgina ${pageNumber} de ${total || "?"} · numeració començant per 1`}
        />
        {total === 0 ? (
          <EmptyState title="Aquest document no té text extret" />
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-border px-5 py-3">
              <Button
                variant="secondary"
                onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
                disabled={pageNumber <= 1}
              >
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
              <Button
                variant="secondary"
                onClick={() => setPageNumber((n) => Math.min(total, n + 1))}
                disabled={pageNumber >= total}
              >
                Següent
              </Button>
            </div>
            {pageLoading ? (
              <LoadingRows />
            ) : (
              <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap px-5 py-4 font-mono text-[12px] leading-relaxed text-ink">
                {page?.text || "(pàgina sense text extret)"}
              </pre>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
