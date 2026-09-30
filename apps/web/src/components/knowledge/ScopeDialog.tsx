"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import { Button, Checkbox, ErrorNotice } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { certificationTitle, serviceLineLabel, type Vocabulary } from "@/lib/knowledge";
import type { Certification } from "@/lib/types";

interface Props {
  /** El diàleg es munta en obrir-lo: la selecció neix de l'abast que ja té. */
  certification: Certification;
  vocabulary: Vocabulary | null;
  /** Està verificada: canviar-ne l'abast n'invalida la verificació. */
  verified?: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * L'abast estructurat d'una certificació: quines línies de servei cobreix.
 * El text lliure es mostra com a nota, però no se'n dedueix res: si no es
 * marca cap línia, l'abast continua sent desconegut.
 */
export function ScopeDialog({ certification, vocabulary, verified = false, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, run } = useMutation();
  const [selected, setSelected] = useState<string[]>(certification.scope_service_lines ?? []);
  const lines = vocabulary?.service_lines ?? [];

  function toggle(code: string) {
    setSelected((current) => (current.includes(code) ? current.filter((item) => item !== code) : [...current, code]));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      await api.patch(path(`/certifications/${certification.id}`), {
        scope_service_lines: selected.length > 0 ? selected : null,
      });
    });
    if (ok) {
      onSaved();
      onClose();
    }
  }

  return (
    <Modal
      open
      title={`Abast de ${certificationTitle(certification, vocabulary)}`}
      description="Marca les línies de servei que cobreix el certificat."
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel·la
          </Button>
          <Button variant="primary" type="submit" form="scope-form" loading={saving}>
            Desa l&apos;abast
          </Button>
        </>
      }
    >
      <form id="scope-form" onSubmit={save} className="space-y-4">
        {error && <ErrorNotice message={error} />}
        {verified && (
          <p role="note" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
            Aquesta certificació està verificada. Si en canvies l&apos;abast tornarà a «pendent de verificar»:
            l&apos;evidència es conserva, però caldrà tornar-la a verificar.
          </p>
        )}
        {certification.scope && (
          <div className="rounded-card border border-border bg-subtle px-4 py-3">
            <p className="text-caption font-medium text-muted">Nota d&apos;abast (text lliure)</p>
            <p className="mt-0.5 text-body text-ink">{certification.scope}</p>
            <p className="mt-1 text-caption text-muted">
              És només una nota: no es fa servir per deduir què cobreix el certificat.
            </p>
          </div>
        )}
        <fieldset className="space-y-2.5">
          <legend className="mb-2 text-[13px] font-medium text-ink">Línies de servei cobertes</legend>
          {lines.map((line) => (
            <Checkbox
              key={line.code}
              label={serviceLineLabel(line.code, vocabulary)}
              checked={selected.includes(line.code)}
              onChange={() => toggle(line.code)}
            />
          ))}
        </fieldset>
        <p className="text-caption text-muted">
          {selected.length === 0
            ? "Sense cap línia marcada, l'abast queda com a desconegut."
            : `${selected.length} ${selected.length === 1 ? "línia marcada" : "línies marcades"}.`}
        </p>
      </form>
    </Modal>
  );
}
