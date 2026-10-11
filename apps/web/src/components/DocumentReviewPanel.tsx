"use client";

import { CheckCircle2, CircleAlert, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { Banner, Button, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { canImportTender } from "@/lib/import-wizard";
import { formatDateTime } from "@/lib/labels";
import type { DocumentReviewState, FinalReadiness } from "@/lib/readiness";

/**
 * Fins on arriba la revisió humana dels plecs, i com dir que ja s'ha fet.
 *
 * Tres estats i cap d'ells diu que els requisits hagin canviat:
 *
 * * **pendents de revisió** — ningú no ha dit res. Que no se n'hagi dit res no
 *   vol dir que hi hagi cap problema; vol dir exactament això, i callar no és
 *   aprovar;
 * * **revisats**, amb la data;
 * * **hi ha canvis des de l'última revisió** — han arribat documents o versions.
 *
 * Marcar-los com a revisats no analitza res. Llegir els plecs i extreure'n els
 * requisits són dues accions diferents, i fondre-les voldria dir que dir «ja
 * me'ls he mirat» engegués una anàlisi —i una factura— que ningú no ha demanat.
 *
 * Llegir els plecs és feina de Decidir, no de Preparar (3B-2, ruling C3): el
 * panell no sap res de la decisió GO ni en depèn.
 */
export function DocumentReviewPanel({
  workspaceId,
  documents,
  onReviewed,
}: {
  workspaceId: string | null;
  documents: number;
  onReviewed?: () => void;
}) {
  const { companyId, membership } = useSession();
  const [state, setState] = useState<DocumentReviewState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // La preparació és la font de veritat i aquest panell només n'ensenya una
  // part: es llegeix, no es calcula aquí.
  useEffect(() => {
    if (!workspaceId || !companyId) return;
    let cancelled = false;
    void api
      .get<FinalReadiness>(`/companies/${companyId}/workspaces/${workspaceId}/final-readiness`)
      .then((readiness) => {
        if (!cancelled) setState(readiness.documents);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, workspaceId]);

  const review = useCallback(async () => {
    if (!workspaceId || !companyId) return;
    setSaving(true);
    setError(null);
    try {
      const readiness = await api.post<FinalReadiness>(
        `/companies/${companyId}/workspaces/${workspaceId}/documents/review`,
        {},
      );
      setState(readiness.documents);
      onReviewed?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No s'ha pogut desar la revisió.");
    } finally {
      setSaving(false);
    }
  }, [companyId, onReviewed, workspaceId]);

  // Sense candidatura no hi ha ningú que hagi de revisar res: la revisió és
  // una afirmació sobre una candidatura, no sobre la licitació.
  if (!workspaceId || documents === 0) return null;
  if (!loaded || !state) return null;

  const canReview = canImportTender(membership?.role);
  const cta = canReview ? (
    <Button
      variant="secondary"
      size="sm"
      loading={saving}
      title="Deixa anotat que algú de l'empresa ha llegit els plecs. No analitza res: l'anàlisi de requisits és una altra acció."
      onClick={() => void review()}
    >
      Marca com revisats
    </Button>
  ) : null;

  if (state.stale) {
    return (
      <Banner level="attention" kicker="Plecs" className="mb-4" action={cta}>
        Hi ha canvis des de l&apos;última revisió. Marcar-los com revisats no analitza els
        documents: l&apos;anàlisi de requisits és una altra acció.
        {error && <span className="block text-caption text-muted">{error}</span>}
      </Banner>
    );
  }

  if (!state.reviewed) {
    return (
      <Banner level="neutral" kicker="Plecs" className="mb-4" action={cta}>
        <span className="inline-flex items-center gap-1.5">
          <CircleAlert size={14} strokeWidth={1.5} aria-hidden className="text-muted" />
          Documents pendents de revisió.
        </span>
        {/* Què vol dir el botó (addendum 3B-2, D6): una anotació humana, mai una anàlisi. */}
        <span className="block text-caption text-muted">
          «Marca com revisats» deixa anotat que algú de l&apos;empresa se&apos;ls ha llegit. No analitza res.
        </span>
        {error && <span className="block text-caption text-muted">{error}</span>}
      </Banner>
    );
  }

  return (
    <p className="mb-4 flex items-center gap-2 text-caption text-muted">
      <CheckCircle2 size={14} strokeWidth={1.5} aria-hidden className="text-muted" />
      <span>
        Documents revisats
        {state.reviewed_at ? ` · ${formatDateTime(state.reviewed_at)}` : ""}
      </span>
    </p>
  );
}

/**
 * «Puja una nova versió» d'un plec concret.
 *
 * Només per a una licitació oberta a mà: els plecs d'una licitació publicada
 * els porta la font, i substituir-los des d'aquí seria escriure per sobre del
 * que ha publicat l'òrgan.
 *
 * Tornar a pujar EXACTAMENT el mateix fitxer no crea cap versió, i es diu: és
 * el cas de dues persones que pugen el mateix, i convertir-ho en «els plecs han
 * canviat» gastaria l'avís que serveix per quan canvien de debò.
 */
export function NewVersionButton({
  tenderId,
  documentId,
  onUploaded,
}: {
  tenderId: string;
  documentId: string;
  onUploaded?: (created: boolean) => void;
}) {
  const { companyId, membership } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const upload = useCallback(
    async (files: FileList | null) => {
      const file = files?.[0];
      if (!file || !companyId) return;
      setSaving(true);
      setNote(null);
      const body = new FormData();
      body.append("file", file);
      try {
        const result = await api.uploadRaw<unknown>(
          `/companies/${companyId}/tenders/${tenderId}/documents/${documentId}/versions`,
          body,
        );
        const created = result.status === 201;
        setNote(created ? null : "Aquest fitxer ja era la versió vigent.");
        onUploaded?.(created);
      } catch (caught) {
        setNote(caught instanceof Error ? caught.message : "No s'ha pogut pujar el fitxer.");
      } finally {
        setSaving(false);
        if (input.current) input.current.value = "";
      }
    },
    [companyId, documentId, onUploaded, tenderId],
  );

  if (!canImportTender(membership?.role)) return null;

  return (
    <span className="inline-flex items-center gap-2">
      <input
        ref={input}
        type="file"
        className="sr-only"
        aria-label={`Nova versió del document ${documentId}`}
        onChange={(event) => void upload(event.target.files)}
      />
      <Button
        variant="ghost"
        size="sm"
        loading={saving}
        onClick={() => input.current?.click()}
      >
        <Upload size={14} strokeWidth={1.5} aria-hidden /> Nova versió
      </Button>
      {note && <StatusPill level="neutral">{note}</StatusPill>}
    </span>
  );
}
