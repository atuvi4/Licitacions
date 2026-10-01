"use client";

import { Button, Input, Select } from "@/components/ui";
import { optionsFrom } from "@/lib/format";
import {
  AREA_KIND_LABELS,
  COMPLETENESS_LABELS,
  MUNICIPALITY_CODE_SYSTEM,
  retypeArea,
  type AreaKind,
  type Coverage,
  type CoverageArea,
  type CoverageCompleteness,
} from "@/lib/knowledge";

interface Props {
  value: Coverage;
  onChange: (next: Coverage) => void;
  disabled?: boolean;
  /** Per distingir dos editors a la mateixa pàgina en els rètols accessibles. */
  name?: string;
}

const NEW_AREA: CoverageArea = { kind: "municipality", label: "", code: null, code_system: MUNICIPALITY_CODE_SYSTEM };

const CAPTION = "text-caption text-muted";

/**
 * Cobertura territorial d'una dada: la completesa i la llista d'àrees, a mà.
 * Sense mapa ni autocompletat: el nom és el que diu l'empresa, i el codi és
 * opcional. «Sense especificar» i cap àrea és l'estat neutre: la dada no
 * afirma res del territori, com abans de tenir aquest camp.
 *
 * Un municipi es codifica sempre amb l'INE: la fila mostra «Codi INE» i cap
 * camp de sistema (el sistema l'hi posa `cleanArea` en enviar). La resta de
 * tipus conserven el sistema de codi lliure. Canviar el tipus d'una fila buida
 * el codi i el sistema (`retypeArea`): un codi només té sentit dins del seu
 * sistema, i un codi INE no pot quedar-se amagat sota un altre tipus.
 */
export function CoverageEditor({ value, onChange, disabled = false, name = "Cobertura territorial" }: Props) {
  const update = (index: number, patch: Partial<CoverageArea>) =>
    onChange({ ...value, areas: value.areas.map((area, i) => (i === index ? { ...area, ...patch } : area)) });
  const retype = (index: number, kind: AreaKind) =>
    onChange({ ...value, areas: value.areas.map((area, i) => (i === index ? retypeArea(area, kind) : area)) });
  const remove = (index: number) => onChange({ ...value, areas: value.areas.filter((_, i) => i !== index) });
  const add = () => onChange({ ...value, areas: [...value.areas, NEW_AREA] });

  return (
    <fieldset className="space-y-3 rounded-card border border-border px-4 py-3" disabled={disabled}>
      <legend className="px-1 text-[13px] font-medium text-ink">{name}</legend>
      <label className="flex flex-col gap-1.5">
        <span className="text-[13px] leading-5 font-medium text-ink">Completesa</span>
        <Select
          value={value.completeness}
          options={optionsFrom(COMPLETENESS_LABELS)}
          onChange={(event) => onChange({ ...value, completeness: event.target.value as CoverageCompleteness })}
        />
        <span className={CAPTION}>
          On és certa aquesta dada. «Sense especificar» i cap àrea vol dir que no se&apos;n diu res, no que no es cobreixi enlloc.
        </span>
      </label>

      {value.areas.length > 0 && (
        <ul className="space-y-3" aria-label={`Àrees de ${name.toLowerCase()}`}>
          {value.areas.map((area, index) => {
            const n = index + 1;
            const municipality = area.kind === "municipality";
            return (
              <li key={index} className={`grid gap-2 sm:items-end ${municipality ? "sm:grid-cols-[9rem_1fr_7rem_auto]" : "sm:grid-cols-[9rem_1fr_6rem_7rem_auto]"}`}>
                <label className="flex flex-col gap-1">
                  <span className={CAPTION}>Tipus</span>
                  <Select
                    aria-label={`Tipus de l'àrea ${n}`}
                    value={area.kind}
                    options={optionsFrom(AREA_KIND_LABELS)}
                    onChange={(event) => retype(index, event.target.value as AreaKind)}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className={CAPTION}>Nom</span>
                  <Input
                    aria-label={`Nom de l'àrea ${n}`}
                    required
                    maxLength={200}
                    placeholder={municipality ? "Banyoles" : "Nom"}
                    value={area.label}
                    onChange={(event) => update(index, { label: event.target.value })}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className={CAPTION}>{municipality ? "Codi INE" : "Codi"}</span>
                  <Input
                    aria-label={municipality ? `Codi INE de l'àrea ${n}` : `Codi de l'àrea ${n}`}
                    maxLength={40}
                    placeholder={municipality ? "17015" : "Codi"}
                    value={area.code ?? ""}
                    onChange={(event) => update(index, { code: event.target.value })}
                  />
                </label>
                {!municipality && (
                  <label className="flex flex-col gap-1">
                    <span className={CAPTION}>Sistema de codi</span>
                    <Input
                      aria-label={`Sistema de codi de l'àrea ${n}`}
                      maxLength={20}
                      placeholder="NUTS, IDESCAT…"
                      value={area.code_system ?? ""}
                      onChange={(event) => update(index, { code_system: event.target.value })}
                    />
                  </label>
                )}
                <Button size="sm" variant="ghost" type="button" aria-label={`Treu l'àrea ${n}`} onClick={() => remove(index)}>
                  Treu
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      <Button size="sm" variant="secondary" type="button" onClick={add}>
        Afegeix una àrea
      </Button>
    </fieldset>
  );
}
