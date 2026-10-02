"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Button, ErrorNotice, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import type { Project, ProjectCPV } from "@/lib/types";

import { errorText } from "./forms";

/** El format que accepta l'API: 8 dígits, opcionalment amb el dígit de control. */
const CPV = /^[0-9]{8}(-[0-9])?$/;

interface Props {
  projectId: string;
  initial: ProjectCPV[];
  /** Sense permís d'edició: només es veuen. */
  canEdit: boolean;
  /** Un canvi de CPV ja és desat al servidor: cal rellegir (verificació inclosa). */
  onChanged: () => void;
}

/**
 * «CPV del projecte»: sota quins codis CPV es va executar. És metadada de
 * l'experiència, separada de les línies de servei acreditades; avui no
 * intervé en cap avaluació. Cada alta o baixa es desa al moment (API pròpia).
 */
export function ProjectCpvEditor({ projectId, initial, canEdit, onChanged }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [cpvs, setCpvs] = useState<ProjectCPV[]>(initial);
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  const base = path(`/projects/${projectId}/cpvs`);

  async function add() {
    const cleaned = code.trim().replace(/\s/g, "");
    if (!CPV.test(cleaned)) {
      setLocal("Codi CPV no vàlid: han de ser 8 dígits, opcionalment amb el dígit de control (p. ex. 64200000-8).");
      return;
    }
    if (cpvs.some((item) => item.cpv_code === cleaned)) {
      setLocal(`El CPV ${cleaned} ja és al projecte.`);
      return;
    }
    setLocal(null);
    const ok = await run(async () => {
      const updated = await api.post<Project>(base, { cpv_code: cleaned, description: description.trim() || null });
      setCpvs(updated?.cpvs ?? [...cpvs, { cpv_code: cleaned, description: description.trim() || null }]);
    });
    if (ok) {
      setCode("");
      setDescription("");
      onChanged();
    }
  }

  async function remove(cpvCode: string) {
    setLocal(null);
    const ok = await run(async () => {
      const updated = await api.delete<Project>(`${base}/${cpvCode}`);
      setCpvs(updated?.cpvs ?? cpvs.filter((item) => item.cpv_code !== cpvCode));
    });
    if (ok) onChanged();
  }

  const message = local ?? errorText(error, fieldErrors);

  return (
    <fieldset className="flex flex-col gap-2" aria-describedby="cpv-help">
      <legend className="text-[13px] leading-5 font-medium text-ink">CPV del projecte</legend>
      <p id="cpv-help" className="text-caption text-muted">
        Sota quins codis CPV es va executar el contracte. No són les línies de servei acreditades i ara mateix no
        intervenen en cap avaluació. Cada canvi es desa al moment.
      </p>
      {message && <ErrorNotice message={message} />}
      {cpvs.length > 0 ? (
        <ul className="flex flex-col divide-y divide-border rounded-card border border-border" aria-label="CPV del projecte">
          {cpvs.map((cpv) => (
            <li key={cpv.cpv_code} className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-center sm:justify-between" data-cpv={cpv.cpv_code}>
              <p className="min-w-0 text-body text-ink">
                <span className="font-mono font-medium">{cpv.cpv_code}</span>
                {cpv.description && <span className="text-muted"> · {cpv.description}</span>}
              </p>
              {canEdit && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="min-h-11 self-end sm:min-h-0 sm:self-auto"
                  aria-label={`Treu el CPV ${cpv.cpv_code}`}
                  disabled={saving}
                  onClick={() => void remove(cpv.cpv_code)}
                >
                  Treu
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-caption text-muted">Cap CPV assignat a aquest projecte.</p>
      )}
      {canEdit && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-[10rem_1fr_auto] sm:items-end">
          <label className="flex flex-col gap-1">
            <span className="text-caption text-muted">Codi CPV</span>
            <Input
              inputMode="numeric"
              placeholder="64200000"
              value={code}
              aria-invalid={local ? true : undefined}
              onChange={(event) => setCode(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void add();
                }
              }}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-caption text-muted">Descripció (opcional)</span>
            <Input value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <Button type="button" variant="secondary" className="min-h-11 sm:min-h-0" loading={saving} onClick={() => void add()}>
            Afegeix
          </Button>
        </div>
      )}
    </fieldset>
  );
}
