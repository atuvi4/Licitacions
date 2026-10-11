"use client";

/**
 * Administració Tuvi · Documents públics oficials d'una licitació (3B-2 §2.6,
 * §7.1.5 punt 5).
 *
 * Només l'operador de plataforma, i només licitacions públiques: afegir per URL
 * (l'allowlist de dominis oficials és del backend; el seu error es mostra tal
 * qual), pujar un fitxer no descarregable amb la pàgina oficial d'origen,
 * verificar amb confirmació explícita, rebutjar / retirar / cedir al feed amb
 * motiu (cedir només si la font ja publica la URL: el 409 del servidor es diu
 * en humà), restaurar, i revisar el rol global amb motiu obligatori, ancorat a
 * la versió vista (`content_sha256`; 409 si ha canviat).
 *
 * Els badges d'origen, aportació i retirada són els de Plecs (`DocumentBadges`,
 * `removedLabel`): cap duplicació. Cap codi intern arriba al text.
 */

import { ChevronRight, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, type ReactNode, useCallback, useState } from "react";

import { Modal } from "@/components/Modal";
import {
  Button,
  Card,
  CardHeader,
  ErrorNotice,
  ErrorState,
  Field,
  Input,
  LinkButton,
  PageSkeleton,
  ResultNotice,
  Select,
  StatusPill,
  Textarea,
} from "@/components/ui";
import { extractedText } from "@/app/(app)/licitacions/[tenderId]/documents";
import { removedLabel } from "@/app/(app)/licitacions/[tenderId]/package";
import { DocumentBadges } from "@/app/(app)/licitacions/[tenderId]/PlecsOverview";
import { useMutation, useResource } from "@/lib/hooks";
import { PLATFORM_HOME, PLATFORM_TENDERS } from "@/lib/platform";
import {
  DOCUMENT_TYPE_OPTIONS,
  OPERATOR_ACTION_LABELS,
  ROLE_OPTIONS,
  canReviewRole,
  classificationPath,
  documentsPath,
  effectiveRoleOf,
  operatorActions,
  platformDocuments,
  type OperatorAction,
  type PlatformClassification,
  type PlatformTenderDocument,
} from "@/lib/platform-documents";
import { documentRoleLabel, documentTypeLabel, formatDateTime, roleSourceLabel, type TenderDocumentType } from "@/lib/tenders";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

/** El que es llegeix de la fitxa pública per a la capçalera; si no arriba, l'identificador. */
interface TenderHeader {
  id: string;
  title: string;
  expediente_number: string | null;
  buyer: { name: string } | null;
}

type ReasonAction = Exclude<OperatorAction, "verify" | "restore">;

type Dialog =
  | { kind: "verify"; document: PlatformTenderDocument }
  | { kind: ReasonAction; document: PlatformTenderDocument }
  | { kind: "role"; document: PlatformTenderDocument };

const DIALOG_TITLES: Record<ReasonAction, string> = {
  reject: "Rebutja l'aportació",
  retire: "Retira el document",
  hand_over: "Cedeix el document al feed",
};

const DIALOG_HELP: Record<ReasonAction, string> = {
  reject: "No és el document publicat oficialment. Queda registrat amb el motiu i no entra a l'anàlisi.",
  retire: "Deixa de ser vigent; no s'esborra res i es pot restaurar. Si té àlies, el primer passa a canònic.",
  hand_over:
    "Només si la font ja publica aquesta URL a la darrera entrada: el sistema ho comprova i, si no, ho refusa. Des d'aleshores el feed en governa el nom, el tipus i la retirada.",
};

/* ------------------------------------------------------------------------- */
/* Diàlegs                                                                    */
/* ------------------------------------------------------------------------- */

/** Verificar: confirmació explícita, sense motiu. */
function VerifyDialog({
  document,
  onClose,
  onDone,
  tenderId,
}: {
  document: PlatformTenderDocument;
  tenderId: string;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const mutation = useMutation();
  async function confirm() {
    const ok = await mutation.run(async () => {
      await platformDocuments.verify(tenderId, document.id);
    });
    if (ok) {
      onClose();
      await onDone();
    }
  }
  return (
    <Modal
      open
      title="Verifica el document"
      description="Verificar vol dir afirmar que és el document publicat oficialment. Passa a ser analitzable i entra a l'anàlisi."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button variant="primary" onClick={() => void confirm()} loading={mutation.saving}>
            Verifica
          </Button>
        </>
      }
    >
      <p className="text-body leading-6 text-ink">
        Confirmes que <strong>«{document.name}»</strong> és el document publicat oficialment?
      </p>
      {document.contribution_source_url && (
        <p className="mt-2 text-caption text-muted break-all">Pàgina d&apos;origen: {document.contribution_source_url}</p>
      )}
      {mutation.error && (
        <div className="mt-3">
          <ErrorNotice message={mutation.error} />
        </div>
      )}
    </Modal>
  );
}

/** Rebutjar, retirar i cedir: motiu obligatori. */
function ReasonDialog({
  kind,
  document,
  tenderId,
  onClose,
  onDone,
}: {
  kind: ReasonAction;
  document: PlatformTenderDocument;
  tenderId: string;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [reason, setReason] = useState("");
  const label = OPERATOR_ACTION_LABELS[kind];
  async function submit() {
    const cleaned = reason.trim();
    if (!cleaned) return;
    const ok = await mutation.run(async () => {
      if (kind === "reject") await platformDocuments.reject(tenderId, document.id, cleaned);
      else if (kind === "retire") await platformDocuments.retire(tenderId, document.id, cleaned);
      else await platformDocuments.handOver(tenderId, document.id, cleaned);
    });
    if (ok) {
      onClose();
      await onDone();
    }
  }
  return (
    <Modal
      open
      title={DIALOG_TITLES[kind]}
      description={DIALOG_HELP[kind]}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button variant="primary" onClick={() => void submit()} loading={mutation.saving} disabled={reason.trim() === ""}>
            {label}
          </Button>
        </>
      }
    >
      <p className="mb-3 text-body text-ink">
        Document: <strong>{document.name}</strong>
      </p>
      <Field label="Motiu" required hint="Queda registrat a l'auditoria amb el teu nom i la data.">
        <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} />
      </Field>
      {mutation.error && (
        <div className="mt-3">
          <ErrorNotice message={mutation.error} />
        </div>
      )}
    </Modal>
  );
}

/** Revisió del rol global: motiu obligatori i ancorada al fitxer vist. */
function RoleReviewDialog({
  document,
  tenderId,
  current,
  onClose,
  onDone,
}: {
  document: PlatformTenderDocument;
  tenderId: string;
  current: string | null;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [role, setRole] = useState(current ?? "other");
  const [reason, setReason] = useState("");
  const sha = document.latest_file?.sha256 ?? null;
  async function submit() {
    const cleaned = reason.trim();
    if (!cleaned || !sha) return;
    const ok = await mutation.run(async () => {
      await platformDocuments.reviewRole(tenderId, document.id, { role, reason: cleaned, content_sha256: sha });
    });
    if (ok) {
      onClose();
      await onDone();
    }
  }
  return (
    <Modal
      open
      title="Revisa el rol del document"
      description="Mana sobre el classificador per a aquesta versió del fitxer. Si el fitxer canvia, la revisió deixa de manar i cal tornar-la a fer."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.saving}>
            Cancel·la
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            loading={mutation.saving}
            disabled={reason.trim() === "" || sha === null}
          >
            Desa la revisió
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <p className="text-body text-ink">
          Document: <strong>{document.name}</strong>
          {sha && <span className="block text-caption text-muted">Versió vista: {sha.slice(0, 12)}…</span>}
        </p>
        <Field label="Rol" required>
          <Select options={ROLE_OPTIONS} value={role} onChange={(event) => setRole(event.target.value)} />
        </Field>
        <Field label="Motiu" required hint="Qui revisa respon del que ha vist: el motiu queda registrat.">
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} />
        </Field>
        {mutation.error && <ErrorNotice message={mutation.error} />}
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Formularis d'incorporació                                                  */
/* ------------------------------------------------------------------------- */

function AddByUrlForm({ tenderId, onAdded }: { tenderId: string; onAdded: (document: PlatformTenderDocument) => Promise<void> }) {
  const mutation = useMutation();
  const [url, setUrl] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<TenderDocumentType>("other");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (url.trim() === "") return;
    let added: PlatformTenderDocument | null = null;
    const ok = await mutation.run(async () => {
      added = await platformDocuments.addByUrl(tenderId, {
        url: url.trim(),
        source_page_url: pageUrl.trim() || null,
        name: name.trim() || null,
        document_type: type,
      });
    });
    if (ok && added) {
      setUrl("");
      setPageUrl("");
      setName("");
      await onAdded(added);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Afegeix per URL"
        description="El sistema el descarrega d'un domini oficial de contractació i el deixa pendent de verificació. Un domini que no sigui oficial es refusa."
      />
      <form onSubmit={(event) => void submit(event)} className="grid gap-3 px-5 py-4 sm:grid-cols-2">
        <Field label="URL del document" required className="sm:col-span-2">
          <Input type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://contractaciopublica.cat/…/document.pdf" />
        </Field>
        <Field label="Pàgina oficial on es publica" hint="Perfil del contractant o fitxa de la plataforma (opcional)." className="sm:col-span-2">
          <Input type="url" value={pageUrl} onChange={(event) => setPageUrl(event.target.value)} />
        </Field>
        <Field label="Nom">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Com el publica l'òrgan" />
        </Field>
        <Field label="Tipus">
          <Select options={DOCUMENT_TYPE_OPTIONS} value={type} onChange={(event) => setType(event.target.value as TenderDocumentType)} />
        </Field>
        {mutation.error && (
          <div className="sm:col-span-2">
            <ErrorNotice message={mutation.error} />
          </div>
        )}
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" size="sm" loading={mutation.saving} disabled={url.trim() === ""}>
            Afegeix per URL
          </Button>
        </div>
      </form>
    </Card>
  );
}

function UploadForm({ tenderId, onAdded }: { tenderId: string; onAdded: (document: PlatformTenderDocument) => Promise<void> }) {
  const mutation = useMutation();
  const [file, setFile] = useState<File | null>(null);
  const [pageUrl, setPageUrl] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<TenderDocumentType>("other");
  const [inputKey, setInputKey] = useState(0);
  const ready = file !== null && pageUrl.trim() !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || !file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("contribution_source_url", pageUrl.trim());
    if (name.trim()) form.append("name", name.trim());
    form.append("document_type", type);
    let added: PlatformTenderDocument | null = null;
    const ok = await mutation.run(async () => {
      added = await platformDocuments.upload(tenderId, form);
    });
    if (ok && added) {
      setFile(null);
      setInputKey((key) => key + 1);
      setPageUrl("");
      setName("");
      await onAdded(added);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Puja un fitxer"
        description="Per a un document oficial que no es pot descarregar directament. Cal dir a quina pàgina oficial es publica; queda pendent de verificació."
      />
      <form onSubmit={(event) => void submit(event)} className="grid gap-3 px-5 py-4 sm:grid-cols-2">
        <Field label="Fitxer" required className="sm:col-span-2">
          <input
            key={inputKey}
            type="file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-body text-ink file:mr-3 file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:py-1.5 file:text-[13px] file:font-medium"
          />
        </Field>
        <Field label="Pàgina oficial on es publica (pujada)" required className="sm:col-span-2">
          <Input type="url" value={pageUrl} onChange={(event) => setPageUrl(event.target.value)} />
        </Field>
        <Field label="Nom">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Tipus">
          <Select options={DOCUMENT_TYPE_OPTIONS} value={type} onChange={(event) => setType(event.target.value as TenderDocumentType)} />
        </Field>
        {mutation.error && (
          <div className="sm:col-span-2">
            <ErrorNotice message={mutation.error} />
          </div>
        )}
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" size="sm" loading={mutation.saving} disabled={!ready}>
            Puja el fitxer
          </Button>
        </div>
      </form>
    </Card>
  );
}

/* ------------------------------------------------------------------------- */
/* Llista                                                                     */
/* ------------------------------------------------------------------------- */

function ProvenanceCell({ document }: { document: PlatformTenderDocument }) {
  const lines: ReactNode[] = [];
  if (document.contributed_at) lines.push(<span key="c">Aportat el {formatDateTime(document.contributed_at)}</span>);
  if (document.contribution_source_url) {
    lines.push(
      <a key="p" href={document.contribution_source_url} target="_blank" rel="noopener noreferrer" className="break-all text-brand-strong hover:text-accent-800">
        Pàgina oficial ↗
      </a>,
    );
  }
  if (document.verified_at) lines.push(<span key="v">Verificat el {formatDateTime(document.verified_at)}</span>);
  if (document.verification_note) lines.push(<span key="n" className="text-ink">{document.verification_note}</span>);
  if (document.retired_reason) lines.push(<span key="r">Motiu de la retirada: {document.retired_reason}</span>);
  if (lines.length === 0) return <span className="text-muted">—</span>;
  return <span className="grid gap-0.5 text-caption text-muted">{lines}</span>;
}

function RoleCell({ document, classification }: { document: PlatformTenderDocument; classification: PlatformClassification | null }) {
  const role = effectiveRoleOf(classification, document.id);
  if (!role) return <span className="text-muted">—</span>;
  const reviews = classification?.reviews.filter((review) => review.tender_document_id === document.id).length ?? 0;
  return (
    <span className="grid gap-0.5">
      <span className="text-ink">{role.role ? documentRoleLabel(role.role) : "sense rol assignat"}</span>
      <span className="text-caption text-muted">
        {roleSourceLabel(role.source)}
        {reviews > 0 && ` · ${reviews} ${reviews === 1 ? "revisió" : "revisions"}`}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------------- */
/* Pàgina                                                                     */
/* ------------------------------------------------------------------------- */

export default function TenderDocumentsPage() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const documents = useResource<PlatformTenderDocument[]>(documentsPath(tenderId));
  const classification = useResource<PlatformClassification>(classificationPath(tenderId));
  const tender = useResource<TenderHeader>(`/tenders/${tenderId}`);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    await Promise.all([documents.reload(), classification.reload()]);
  }, [documents, classification]);

  const done = useCallback(
    async (message: string) => {
      setNotice(message);
      await refresh();
    },
    [refresh],
  );

  // Restaurar no demana motiu ni confirmació: desfà una retirada i es veu a l'acte.
  const restore = useCallback(
    async (row: PlatformTenderDocument) => {
      try {
        await platformDocuments.restore(tenderId, row.id);
        await done(`«${row.name}» restaurat.`);
      } catch (caught) {
        setNotice(caught instanceof Error ? caught.message : "No s'ha pogut restaurar el document.");
      }
    },
    [done, tenderId],
  );

  if (documents.loading && !documents.data) {
    return <PageSkeleton variant="table" title="Documents públics" context="carregant els documents…" />;
  }
  if (!documents.data) {
    return (
      <ErrorState
        title="No s'han pogut carregar els documents d'aquesta licitació"
        description={documents.error ?? "La connexió amb el servei ha fallat."}
        action={
          <Button variant="primary" size="md" onClick={() => void documents.reload()}>
            Torna-ho a provar
          </Button>
        }
        secondary={
          <LinkButton href={PLATFORM_HOME} variant="secondary" size="md">
            Torna a Empreses
          </LinkButton>
        }
        technical={{ code: documents.status !== null ? String(documents.status) : null, method: "GET" }}
      />
    );
  }

  const rows = documents.data;
  const title = tender.data?.title ?? `Licitació ${tenderId}`;
  const subtitle = [tender.data?.expediente_number, tender.data?.buyer?.name].filter(Boolean).join(" · ");
  const pending = rows.filter((row) => row.contribution_status === "pending_verification" && !row.removed_at).length;
  const gaps = classification.data?.gaps ?? [];
  const closeDialog = () => setDialog(null);

  return (
    <div className="space-y-6">
      <nav aria-label="Ruta" className="flex items-center gap-1.5 text-[13px] text-muted">
        <Link href={PLATFORM_HOME} className="text-brand-strong hover:text-accent-800">
          Empreses
        </Link>
        <ChevronRight size={13} aria-hidden />
        <Link href={PLATFORM_TENDERS} className="text-brand-strong hover:text-accent-800">
          Licitacions
        </Link>
        <ChevronRight size={13} aria-hidden />
        <span className="text-ink">{tender.data?.expediente_number ?? tenderId}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className={KICKER}>
            <ShieldCheck size={13} strokeWidth={1.5} aria-hidden className="mr-1 inline" />
            Documents públics oficials
          </p>
          <h1 className="mt-1 font-heading text-page font-semibold text-ink">{title}</h1>
          {subtitle && <p className="mt-1 text-body text-muted">{subtitle}</p>}
          <p className="mt-2 max-w-[70ch] text-[13px] leading-5 text-muted">
            Documents del paquet públic que el feed no enumera. Tot el que s&apos;incorpora queda pendent fins que
            l&apos;operador el verifica; les empreses ho veuen amb el seu estat, però només el verificat entra a
            l&apos;anàlisi.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {pending > 0 && (
            <StatusPill level="attention">
              {pending} {pending === 1 ? "pendent de verificació" : "pendents de verificació"}
            </StatusPill>
          )}
          <LinkButton href={`/licitacions/${tenderId}`} variant="secondary" size="sm">
            Fitxa pública ↗
          </LinkButton>
        </div>
      </header>

      {notice && <ResultNotice tone="success" message={notice} />}

      {gaps.length > 0 && (
        <p className="text-[13px] text-muted">
          Forats oberts del paquet segons la classificació vigent: {gaps.length}. Un document que els resolgui
          tanca el forat en verificar-lo.
        </p>
      )}

      <Card>
        <CardHeader
          title={`Documents (${rows.length})`}
          description="Tots, retirats inclosos, amb l'origen, l'estat de l'aportació i el rol efectiu que llegeix l'anàlisi."
        />
        {rows.length === 0 ? (
          <p className="px-5 py-6 text-body text-muted">Cap document: ni del feed ni aportat. Afegeix-ne un per URL o puja&apos;l.</p>
        ) : (
          <div className="overflow-x-auto">
            <table aria-label="Documents públics de la licitació" className="w-full text-[13.5px]">
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted">
                  <th scope="col" className="px-5 py-2.5 font-medium">Document</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Lectura</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Rol efectiu</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Aportació</th>
                  <th scope="col" className="px-5 py-2.5 text-right font-medium">Accions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const removed = removedLabel(row);
                  const actions = operatorActions(row);
                  return (
                    <tr key={row.id} className="border-b border-border align-top last:border-b-0" data-removed={removed ? "true" : undefined}>
                      <td className="px-5 py-3">
                        <span className="block font-medium text-ink">{row.name}</span>
                        <span className="block text-caption text-muted">{documentTypeLabel(row.document_type)}</span>
                        <DocumentBadges document={row} documents={rows} />
                        {removed && <span className="mt-1 block text-caption text-muted">{removed}</span>}
                      </td>
                      <td className="px-3 py-3 text-muted">
                        {extractedText(row.latest_file)}
                        {row.latest_file?.sha256 && (
                          <span className="block font-mono text-[11px] text-muted">{row.latest_file.sha256.slice(0, 12)}…</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        <RoleCell document={row} classification={classification.data} />
                      </td>
                      <td className="px-3 py-3">
                        <ProvenanceCell document={row} />
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {actions.map((action) => (
                            <Button
                              key={action}
                              variant={action === "verify" ? "primary" : "secondary"}
                              size="sm"
                              onClick={
                                action === "restore"
                                  ? () => void restore(row)
                                  : () => setDialog({ kind: action, document: row })
                              }
                            >
                              {OPERATOR_ACTION_LABELS[action]}
                            </Button>
                          ))}
                          {canReviewRole(row) && (
                            <Button variant="ghost" size="sm" onClick={() => setDialog({ kind: "role", document: row })}>
                              Revisa el rol
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <AddByUrlForm tenderId={tenderId} onAdded={(added) => done(`«${added.name}» incorporat: pendent de verificació.`)} />
        <UploadForm tenderId={tenderId} onAdded={(added) => done(`«${added.name}» pujat: pendent de verificació.`)} />
      </div>

      {dialog?.kind === "verify" && (
        <VerifyDialog document={dialog.document} tenderId={tenderId} onClose={closeDialog} onDone={() => done(`«${dialog.document.name}» verificat: ja és analitzable.`)} />
      )}
      {dialog && (dialog.kind === "reject" || dialog.kind === "retire" || dialog.kind === "hand_over") && (
        <ReasonDialog
          kind={dialog.kind}
          document={dialog.document}
          tenderId={tenderId}
          onClose={closeDialog}
          onDone={() => done(`«${dialog.document.name}»: fet.`)}
        />
      )}
      {dialog?.kind === "role" && (
        <RoleReviewDialog
          document={dialog.document}
          tenderId={tenderId}
          current={effectiveRoleOf(classification.data, dialog.document.id)?.role ?? null}
          onClose={closeDialog}
          onDone={() => done(`Rol de «${dialog.document.name}» revisat.`)}
        />
      )}
    </div>
  );
}
