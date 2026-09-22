"use client";

import { FileText, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Modal } from "@/components/Modal";
import { useSession } from "@/components/SessionProvider";
import { Banner, Button, Field, Input, Select, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  DEFAULT_DOCUMENT_TYPE,
  EMPTY_STEP1,
  UPLOAD_DOCUMENT_TYPES,
  createPayload,
  documentTypeLabel,
  failedRowState,
  fileSize,
  newTokens,
  step1Error,
  updatePayload,
  uploadRowState,
  type BuyerChoice,
  type Step1Form,
  type UploadDocumentType,
  type UploadRowState,
  type WizardTokens,
} from "@/lib/import-wizard";
import type { BuyerSummary, TenderDetail, TenderDocumentFileSummary } from "@/lib/tenders";

interface CreateResult {
  created: boolean;
  tender: TenderDetail;
}

interface UpdateResult {
  revised: boolean;
  revision: number;
  tender: TenderDetail;
}

interface UploadResult {
  id: string;
  name: string;
  stored: boolean;
  content_reused: boolean;
  file: TenderDocumentFileSummary;
}

interface Row {
  key: string;
  name: string;
  size: number | null;
  /**
   * El fitxer, mentre encara no s'ha pujat. Es guarda perquè el tipus es
   * declara ABANS de pujar-lo i perquè un reintent ha de poder tornar a
   * enviar exactament el mateix.
   */
  file: File | null;
  /** Què és aquest document. Per fitxer, no per pujada. */
  documentType: UploadDocumentType;
  state: UploadRowState;
  /** `staged` = triat i encara no enviat. */
  phase: "staged" | "uploading" | "done" | "failed";
}

/**
 * «Importa licitació»: dades primer, documents després.
 *
 * Dues coses que aquest component NO fa, i que són decisions, no oblits:
 *
 * **No desfà la licitació si el pas 2 va malament.** Crear-la ja és una operació
 * vàlida i idempotent: si un fitxer falla, el que s'ha de poder fer és
 * tornar-ho a provar, no perdre el que ja estava bé.
 *
 * **En acabar no crea cap candidatura ni decideix res.** La licitació ja surt a
 * Oportunitats amb el seu pre-fit. Presentar-s'hi o no és una decisió humana i
 * es pren després, al seu lloc.
 */
export function ImportTenderWizard({
  open,
  onClose,
  onFinished,
}: {
  open: boolean;
  onClose: () => void;
  onFinished?: (tenderId: string) => void;
}) {
  const { companyId } = useSession();
  const [tokens, setTokens] = useState<WizardTokens>(() => newTokens());
  const [step, setStep] = useState<1 | 2>(1);
  const [form, setForm] = useState<Step1Form>(EMPTY_STEP1);
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [revision, setRevision] = useState(1);
  const [buyers, setBuyers] = useState<BuyerSummary[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Els tokens es generen en OBRIR l'assistent i no es tornen a generar mentre
  // està obert: si canviessin, un reintent crearia una segona licitació en
  // comptes de recuperar la mateixa.
  useEffect(() => {
    if (!open) return;
    setTokens(newTokens());
    setStep(1);
    setForm(EMPTY_STEP1);
    setTender(null);
    setRevision(1);
    setRows([]);
    setError(null);
  }, [open]);

  // El catàleg d'organismes és el de l'empresa: els privats d'una altra no hi
  // són, i canviar d'empresa canvia la llista.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void api
      .get<BuyerSummary[]>(`/companies/${companyId}/buyers?limit=200`)
      .then((data) => {
        if (!cancelled) setBuyers(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setBuyers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, companyId]);

  const validation = useMemo(() => step1Error(form), [form]);

  const goToDocuments = useCallback(async () => {
    if (validation) {
      setError(validation);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (tender) {
        // Tornar enrere i corregir NO crea una segona licitació: es corregeix
        // la mateixa, amb la revisió que hem llegit.
        const result = await api.patch<UpdateResult>(
          `/companies/${companyId}/tenders/${tender.id}`,
          updatePayload(form, tokens, revision),
        );
        setTender(result.tender);
        setRevision(result.revision);
      } else {
        const result = await api.post<CreateResult>(
          `/companies/${companyId}/tenders`,
          createPayload(form, tokens),
        );
        setTender(result.tender);
      }
      setStep(2);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No s'ha pogut desar la licitació.");
    } finally {
      setBusy(false);
    }
  }, [companyId, form, revision, tender, tokens, validation]);

  // Triar fitxers NO els puja: els posa en espera perquè qui els puja pugui dir
  // què són abans d'enviar-los. Endevinar-ho pel nom seria inventar-se una
  // classificació que ningú no ha fet, i el tipus decideix si el document entra
  // a l'anàlisi de requisits.
  const stage = useCallback((files: FileList | null) => {
    if (!files) return;
    setError(null);
    setRows((current) => [
      ...current,
      ...Array.from(files).map((file) => ({
        key: `${file.name}-${file.size}-${crypto.randomUUID()}`,
        name: file.name,
        size: file.size,
        file,
        documentType: DEFAULT_DOCUMENT_TYPE,
        state: { tone: "info" as const, stored: "Per pujar", analysis: "" },
        phase: "staged" as const,
      })),
    ]);
    if (fileInput.current) fileInput.current.value = "";
  }, []);

  const setType = useCallback((key: string, documentType: UploadDocumentType) => {
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, documentType } : row)),
    );
  }, []);

  const send = useCallback(
    async (row: Row) => {
      if (!tender || !row.file) return;
      setRows((current) =>
        current.map((item) =>
          item.key === row.key
            ? {
                ...item,
                phase: "uploading",
                state: { tone: "info", stored: "Pujant…", analysis: "" },
              }
            : item,
        ),
      );
      const body = new FormData();
      body.append("file", row.file);
      // El tipus viatja SEMPRE, també quan és `other`: el servidor no ha
      // d'endevinar que no s'ha dit res.
      body.append("document_type", row.documentType);
      try {
        const result = await api.upload<UploadResult>(
          `/companies/${companyId}/tenders/${tender.id}/documents`,
          body,
        );
        setRows((current) =>
          current.map((item) =>
            item.key === row.key
              ? {
                  ...item,
                  name: result.name,
                  size: result.file.file_size,
                  state: uploadRowState(result.file),
                  phase: "done",
                  // El fitxer es conserva: si després cal reintentar-lo, s'ha
                  // de poder enviar el mateix i amb el mateix tipus.
                  file: item.file,
                }
              : item,
          ),
        );
      } catch (caught) {
        const message =
          caught instanceof Error ? caught.message : "No s'ha pogut pujar el fitxer.";
        setRows((current) =>
          current.map((item) =>
            item.key === row.key
              ? { ...item, state: failedRowState(message), phase: "failed" }
              : item,
          ),
        );
      }
    },
    [companyId, tender],
  );

  const sendAll = useCallback(async () => {
    for (const row of rows.filter((item) => item.phase === "staged")) {
      await send(row);
    }
  }, [rows, send]);

  const pick = useCallback(() => fileInput.current?.click(), []);
  const staged = rows.filter((row) => row.phase === "staged").length;

  return (
    <Modal
      open={open}
      size="lg"
      title="Importa licitació"
      description={
        step === 1
          ? "Dona d'alta un expedient que cap font no publica. Només el títol és obligatori."
          : "Afegeix els plecs. Es guarden tots; d'alguns formats no en sabem llegir el text."
      }
      onClose={onClose}
      footer={
        step === 1 ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel·la
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void goToDocuments()}>
              {tender ? "Desa i continua" : "Crea i continua"}
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={() => setStep(1)}>
              Enrere
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (tender) onFinished?.(tender.id);
                onClose();
              }}
            >
              Veure a Oportunitats
            </Button>
          </>
        )
      }
    >
      {error && (
        <Banner level="blocking" kicker="No s'ha desat" className="mb-4" action={null}>
          {error}
        </Banner>
      )}

      {step === 1 ? (
        <div className="grid gap-4">
          <Field label="Títol" required>
            <Input
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              placeholder="Servei de manteniment integral"
              autoFocus
            />
          </Field>
          <Field label="Número d'expedient" hint="Tal com el fa servir l'òrgan. No identifica res.">
            <Input
              value={form.expediente_number}
              onChange={(event) => setForm({ ...form, expediente_number: event.target.value })}
              placeholder="2026/1234"
            />
          </Field>
          <Field label="Organisme">
            <Select
              aria-label="Organisme"
              value={form.buyerChoice}
              onChange={(event) =>
                setForm({ ...form, buyerChoice: event.target.value as BuyerChoice })
              }
              options={[
                { value: "unknown", label: "Encara no se sap" },
                { value: "existing", label: "Tria'n un del catàleg" },
                { value: "manual", label: "Escriu-ne un de nou" },
              ]}
            />
          </Field>
          {form.buyerChoice === "existing" && (
            <Field label="Organisme del catàleg">
              <Select
                aria-label="Organisme del catàleg"
                value={form.buyerId}
                onChange={(event) => setForm({ ...form, buyerId: event.target.value })}
                placeholder="Tria un organisme"
                options={buyers.map((buyer) => ({ value: buyer.id, label: buyer.name }))}
              />
            </Field>
          )}
          {form.buyerChoice === "manual" && (
            <Field label="Nom de l'organisme" required>
              <Input
                value={form.buyerName}
                onChange={(event) => setForm({ ...form, buyerName: event.target.value })}
                placeholder="Ajuntament de…"
              />
            </Field>
          )}
        </div>
      ) : (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileInput}
              type="file"
              multiple
              className="sr-only"
              aria-label="Fitxers"
              onChange={(event) => stage(event.target.files)}
            />
            <Button variant="secondary" onClick={pick}>
              <Upload size={15} strokeWidth={1.5} aria-hidden /> Tria fitxers
            </Button>
            {staged > 0 && (
              <Button variant="primary" onClick={() => void sendAll()}>
                Puja {staged === 1 ? "el fitxer" : `els ${staged} fitxers`}
              </Button>
            )}
            <p className="text-caption text-muted">
              PDF i text s&apos;analitzen. La resta es guarden tal com arriben.
            </p>
          </div>

          {rows.length === 0 ? (
            <p className="rounded-card border border-dashed border-border px-4 py-8 text-center text-body text-muted">
              Encara no hi ha documents.
            </p>
          ) : (
            <ul className="grid gap-2">
              {rows.map((row) => (
                <li
                  key={row.key}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border px-4 py-3"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <FileText size={15} strokeWidth={1.5} aria-hidden className="text-muted" />
                    <span className="truncate text-body text-ink">{row.name}</span>
                    <span className="text-caption text-muted">{fileSize(row.size)}</span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    {row.phase === "staged" ? (
                      <Select
                        aria-label={`Tipus de ${row.name}`}
                        value={row.documentType}
                        onChange={(event) =>
                          setType(row.key, event.target.value as UploadDocumentType)
                        }
                        options={UPLOAD_DOCUMENT_TYPES.map((option) => ({ ...option }))}
                      />
                    ) : (
                      <span className="text-caption text-muted">
                        {documentTypeLabel(row.documentType)}
                      </span>
                    )}
                    <StatusPill
                      level={
                        row.state.tone === "error"
                          ? "blocking"
                          : row.phase === "done"
                            ? "ready"
                            : "neutral"
                      }
                    >
                      {row.state.stored}
                    </StatusPill>
                    {row.state.analysis && (
                      <span className="text-caption text-muted">{row.state.analysis}</span>
                    )}
                    {row.phase === "failed" && (
                      // El reintent envia el MATEIX fitxer amb el MATEIX tipus:
                      // tornar a preguntar-ho seria fer repetir una decisió que
                      // ja s'havia pres.
                      <Button variant="ghost" size="sm" onClick={() => void send(row)}>
                        Reintenta
                      </Button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {rows.some((row) => row.phase === "failed") && (
            <Banner level="attention" kicker="Alguns fitxers" action={null}>
              Els que s&apos;han guardat hi continuen sent. Reintenta només els que han fallat.
            </Banner>
          )}
        </div>
      )}
    </Modal>
  );
}
