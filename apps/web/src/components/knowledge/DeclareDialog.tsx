"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { useCompanyPath } from "@/components/SessionProvider";
import { Button, Checkbox, Combobox, ErrorNotice, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { optionsFrom } from "@/lib/format";
import { useMutation } from "@/lib/hooks";
import {
  CAPABILITY_STATUS_LABELS,
  DELIVERY_MODE_LABELS,
  FACT_KEYS,
  FACT_LABELS,
  KIND_LABELS,
  LIFECYCLE_LABELS,
  PARTNER_ROLE_LABELS,
  capabilityLabel,
  levelLabel,
  serviceLineLabel,
  standardLabel,
  type CapabilityRecord,
  type KnowledgeRecords,
  type OperatingFactRecord,
  type PartnerRecord,
  type PartnerRoleRecord,
  type ProductRecord,
  type SolutionRecord,
  type Vocabulary,
} from "@/lib/knowledge";

/** Què es declara o s'edita. Amb `record`, és una edició. */
export type DeclareTarget =
  | { kind: "certification"; standardCode?: string }
  | { kind: "service"; serviceLineCode?: string }
  | { kind: "operating_fact"; factKey?: string; record?: OperatingFactRecord }
  | { kind: "solution"; record?: SolutionRecord }
  | { kind: "solution_capability"; solutionId?: string; record?: CapabilityRecord }
  | { kind: "product"; record?: ProductRecord }
  | { kind: "partner"; record?: PartnerRecord }
  | { kind: "partner_role"; partnerId: string; record?: PartnerRoleRecord };

type Form = Record<string, string>;

interface Props {
  /** El diàleg es munta en obrir-lo: l'estat del formulari neix d'aquí. */
  target: DeclareTarget;
  vocabulary: Vocabulary | null;
  records: KnowledgeRecords | null;
  /** Eliminar definitivament: només propietari i administració. */
  canDelete: boolean;
  /** La dada que s'edita està verificada: editar-la n'invalida la verificació. */
  verified?: boolean;
  onClose: () => void;
  onSaved: () => void;
}

const OTHER = "__other__";
/** Articles per al títol: «Declara una certificació», «Edita el fet operatiu». */
const FEMININE = new Set(["certification", "solution", "solution_capability"]);

function initialForm(target: DeclareTarget): Form {
  const record = "record" in target ? target.record : undefined;
  const provenance = { lifecycle: record?.lifecycle ?? "current", valid_until: record?.valid_until ?? "" };
  switch (target.kind) {
    case "certification":
      return { standard_code: target.standardCode ?? "", level: "", name: "", expires_at: "", lifecycle: "current" };
    case "service":
      return { name: "", service_line_code: target.serviceLineCode ?? "", delivery_mode: "" };
    case "operating_fact":
      return {
        key: target.record?.key ?? target.factKey ?? "",
        value: target.record ? String(target.record.value) : "true",
        detail: target.record?.detail ?? "",
        ...provenance,
      };
    case "solution":
      return {
        name: target.record?.name ?? "",
        service_line_code: target.record?.service_line_code ?? "",
        vendor: target.record?.vendor ?? "",
        edition: target.record?.edition ?? "",
        ...provenance,
      };
    case "solution_capability":
      return {
        solution_id: target.record?.solution_id ?? target.solutionId ?? "",
        capability_code: target.record?.capability_code ?? "",
        status: target.record?.status ?? "supported",
        value_number: target.record?.value_number ? String(Number(target.record.value_number)) : "",
        value_level: target.record?.value_level ?? "",
        limits_note: target.record?.limits_note ?? "",
        ...provenance,
      };
    case "product":
      return {
        manufacturer: target.record?.manufacturer ?? "",
        model: target.record?.model ?? "",
        service_line_code: target.record?.service_line_code ?? "",
        ...provenance,
      };
    case "partner":
      return {
        name: target.record?.name ?? "",
        tax_id: target.record?.tax_id ?? "",
        role: "",
        role_service_line_code: "",
        role_territory: "",
        ...provenance,
      };
    case "partner_role":
      return {
        role: target.record?.role ?? "",
        service_line_code: target.record?.service_line_code ?? "",
        territory: target.record?.territory ?? "",
        ...provenance,
      };
  }
}

const blank = (value: string | undefined) => (value && value.trim() !== "" ? value.trim() : null);

/**
 * Declarar coneixement actual a mà, o editar-lo. El que es declara aquí neix
 * «declarat» i pendent d'evidència: el formulari no té cap camp de
 * verificació, i l'API tampoc n'accepta.
 */
export function DeclareDialog({ target, vocabulary, records, canDelete, verified = false, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [form, setForm] = useState<Form>(() => initialForm(target));
  const [scope, setScope] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const record = "record" in target ? target.record : undefined;
  const editing = Boolean(record);
  const set = (name: string) => (event: { target: { value: string } }) => setForm((current) => ({ ...current, [name]: event.target.value }));
  const setValue = (name: string) => (value: string) => setForm((current) => ({ ...current, [name]: value }));

  const lineOptions = (vocabulary?.service_lines ?? []).map((line) => ({
    value: line.code,
    label: serviceLineLabel(line.code, vocabulary),
  }));
  const provenance = () => ({ lifecycle: form.lifecycle || "current", valid_until: blank(form.valid_until) });

  const solutions = records?.solutions ?? [];
  const solution = solutions.find((item) => item.id === form.solution_id);
  const declaredCodes = new Set(
    (records?.solution_capabilities ?? []).filter((item) => item.solution_id === form.solution_id).map((item) => item.capability_code),
  );
  const ofLine = (vocabulary?.capabilities ?? []).filter((item) => item.service_line_code === solution?.service_line_code);
  const capabilityOptions = (ofLine.length > 0 ? ofLine : (vocabulary?.capabilities ?? []))
    .filter((item) => editing || !declaredCodes.has(item.code))
    .map((item) => ({ value: item.code, label: capabilityLabel(item.code, vocabulary) }));
  const capability = vocabulary?.capabilities.find((item) => item.code === form.capability_code);

  const standard = vocabulary?.standards.find((item) => item.code === form.standard_code);
  const usedFacts = new Set((records?.operating_facts ?? []).map((item) => item.key));
  const factOptions = FACT_KEYS.filter((key) => editing || !usedFacts.has(key)).map((key) => ({ value: key, label: FACT_LABELS[key] }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const recordsUrl = path(`/knowledge/records/${target.kind}`);
    const ok = await run(async () => {
      switch (target.kind) {
        case "certification": {
          const code = form.standard_code && form.standard_code !== OTHER ? form.standard_code : null;
          const auto = code ? [standardLabel(code, vocabulary), levelLabel(form.level)].filter(Boolean).join(" ") : "";
          await api.post(path("/certifications"), {
            name: blank(form.name) ?? auto,
            standard_code: code,
            level: code ? blank(form.level) : null,
            expires_at: blank(form.expires_at),
            lifecycle: form.lifecycle || "current",
            ...(scope.length > 0 ? { scope_service_lines: scope } : {}),
          });
          return;
        }
        case "service":
          await api.post(path("/services"), {
            name: form.name.trim(),
            service_line_code: form.service_line_code,
            delivery_mode: blank(form.delivery_mode),
          });
          return;
        case "operating_fact": {
          const body = { value: form.value === "true", detail: blank(form.detail), ...provenance() };
          if (record) await api.patch(`${recordsUrl}/${record.id}`, body);
          else await api.post(recordsUrl, { key: form.key, ...body });
          return;
        }
        case "solution": {
          const body = {
            name: form.name.trim(),
            service_line_code: form.service_line_code,
            vendor: blank(form.vendor),
            edition: blank(form.edition),
            ...provenance(),
          };
          if (record) await api.patch(`${recordsUrl}/${record.id}`, body);
          else await api.post(recordsUrl, body);
          return;
        }
        case "solution_capability": {
          const body = {
            status: form.status,
            value_number: capability?.value_type === "number" ? blank(form.value_number) : null,
            value_level: capability?.value_type === "ordinal" ? blank(form.value_level) : null,
            limits_note: blank(form.limits_note),
            ...provenance(),
          };
          if (record) await api.patch(`${recordsUrl}/${record.id}`, body);
          else await api.post(recordsUrl, { solution_id: form.solution_id, capability_code: form.capability_code, ...body });
          return;
        }
        case "product": {
          const body = {
            manufacturer: form.manufacturer.trim(),
            model: form.model.trim(),
            service_line_code: blank(form.service_line_code),
            ...provenance(),
          };
          if (record) await api.patch(`${recordsUrl}/${record.id}`, body);
          else await api.post(recordsUrl, body);
          return;
        }
        case "partner": {
          const body = { name: form.name.trim(), tax_id: blank(form.tax_id), ...provenance() };
          if (record) {
            await api.patch(`${recordsUrl}/${record.id}`, body);
          } else {
            const roles = form.role
              ? [{ role: form.role, service_line_code: blank(form.role_service_line_code), territory: blank(form.role_territory) }]
              : [];
            await api.post(recordsUrl, { ...body, roles });
          }
          return;
        }
        case "partner_role": {
          const body = {
            role: form.role,
            service_line_code: blank(form.service_line_code),
            territory: blank(form.territory),
            ...provenance(),
          };
          if (record) await api.patch(`${recordsUrl}/${record.id}`, body);
          else await api.post(recordsUrl, { partner_id: target.partnerId, ...body });
          return;
        }
      }
    });
    if (ok) {
      onSaved();
      onClose();
    }
  }

  async function remove() {
    if (!record) return;
    const ok = await run(async () => {
      await api.delete(path(`/knowledge/records/${target.kind}/${record.id}`));
    });
    if (ok) {
      onSaved();
      onClose();
    }
  }

  const noun = KIND_LABELS[target.kind];
  const feminine = FEMININE.has(target.kind);
  const title = editing ? `Edita ${feminine ? "la" : "el"} ${noun}` : `Declara ${feminine ? "una" : "un"} ${noun}`;
  const declarable = target.kind !== "certification" && target.kind !== "service";

  return (
    <Modal
      open
      title={title}
      description={
        editing
          ? undefined
          : "Quedarà com a declarat i pendent d'evidència. Es verifica després, enllaçant-hi el document que ho acredita."
      }
      onClose={onClose}
      footer={
        <>
          {editing && canDelete && (
            <Button variant="ghost" className="mr-auto" onClick={() => (confirmDelete ? void remove() : setConfirmDelete(true))} disabled={saving}>
              {confirmDelete ? "Segur? Elimina-ho definitivament" : "Elimina"}
            </Button>
          )}
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel·la
          </Button>
          <Button variant="primary" type="submit" form="declare-form" loading={saving}>
            {editing ? "Desa" : "Declara"}
          </Button>
        </>
      }
    >
      <form id="declare-form" onSubmit={submit} className="space-y-4">
        {error && <ErrorNotice message={error} />}
        {editing && verified && (
          <p role="note" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
            Aquesta dada està verificada. Si en canvies el contingut tornarà a «pendent de verificar»: l&apos;evidència es conserva, però caldrà tornar-la a verificar.
          </p>
        )}

        {target.kind === "certification" && (
          <>
            <Field label="Norma" required>
              <Select
                required
                value={form.standard_code ?? ""}
                placeholder="Tria una norma"
                options={[
                  ...(vocabulary?.standards ?? []).map((item) => ({ value: item.code, label: standardLabel(item.code, vocabulary) })),
                  { value: OTHER, label: "Una altra certificació" },
                ]}
                onChange={(event) => setForm((current) => ({ ...current, standard_code: event.target.value, level: "" }))}
              />
            </Field>
            {standard?.levels && (
              <Field label="Nivell" required error={fieldErrors.level}>
                <Select
                  required
                  value={form.level ?? ""}
                  placeholder="Tria el nivell"
                  options={standard.levels.map((level) => ({ value: level, label: levelLabel(level) }))}
                  onChange={set("level")}
                />
              </Field>
            )}
            {form.standard_code === OTHER && (
              <Field label="Nom de la certificació" required error={fieldErrors.name}>
                <Input required value={form.name ?? ""} onChange={set("name")} />
              </Field>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Data de caducitat" error={fieldErrors.expires_at}>
                <Input type="date" value={form.expires_at ?? ""} onChange={set("expires_at")} />
              </Field>
              <Field label="Situació">
                <Select
                  value={form.lifecycle ?? "current"}
                  options={[
                    { value: "current", label: "Vigent" },
                    { value: "in_progress", label: "En tràmit" },
                  ]}
                  onChange={set("lifecycle")}
                />
              </Field>
            </div>
            <fieldset className="space-y-2.5">
              <legend className="mb-2 text-[13px] font-medium text-ink">Abast: línies de servei cobertes</legend>
              {lineOptions.map((line) => (
                <Checkbox
                  key={line.value}
                  label={line.label}
                  checked={scope.includes(line.value)}
                  onChange={() =>
                    setScope((current) => (current.includes(line.value) ? current.filter((item) => item !== line.value) : [...current, line.value]))
                  }
                />
              ))}
              <p className="text-caption text-muted">Si no en marques cap, l&apos;abast queda com a desconegut i es pot completar després.</p>
            </fieldset>
          </>
        )}

        {target.kind === "service" && (
          <>
            <Field label="Nom del servei" required error={fieldErrors.name}>
              <Input required minLength={2} value={form.name ?? ""} onChange={set("name")} placeholder="Centraleta al núvol" />
            </Field>
            <Field label="Línia de servei" required hint="El que permet comparar-lo amb el que demana un plec. Escriu per trobar-la.">
              <Combobox required value={form.service_line_code ?? ""} placeholder="Tria una línia" options={lineOptions} onChange={setValue("service_line_code")} />
            </Field>
            <Field label="Com es presta">
              <Select value={form.delivery_mode ?? ""} placeholder="Sense especificar" options={optionsFrom(DELIVERY_MODE_LABELS)} onChange={set("delivery_mode")} />
            </Field>
          </>
        )}

        {target.kind === "operating_fact" && (
          <>
            <Field label="Fet" required>
              <Select required disabled={editing} value={form.key ?? ""} placeholder="Tria un fet" options={factOptions} onChange={set("key")} />
            </Field>
            <Field label="L'empresa ho compleix?" required hint="Dir que no també és informació: és diferent de no saber-ho.">
              <Select
                value={form.value ?? "true"}
                options={[
                  { value: "true", label: "Sí" },
                  { value: "false", label: "No" },
                ]}
                onChange={set("value")}
              />
            </Field>
            <Field label="Aclariment" hint="Curt i sense imports: «CPD propi en un centre Tier-3».">
              <Textarea rows={2} maxLength={500} value={form.detail ?? ""} onChange={set("detail")} />
            </Field>
          </>
        )}

        {target.kind === "solution" && (
          <>
            <Field label="Nom de la solució" required error={fieldErrors.name}>
              <Input required value={form.name ?? ""} onChange={set("name")} placeholder="Centraleta cloud" />
            </Field>
            <Field label="Línia de servei" required>
              <Combobox required value={form.service_line_code ?? ""} placeholder="Tria una línia" options={lineOptions} onChange={setValue("service_line_code")} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Fabricant">
                <Input value={form.vendor ?? ""} onChange={set("vendor")} />
              </Field>
              <Field label="Edició o llicència" hint="Una funció pot ser només d'una edició.">
                <Input value={form.edition ?? ""} onChange={set("edition")} />
              </Field>
            </div>
          </>
        )}

        {target.kind === "solution_capability" && (
          <>
            <Field label="Solució" required>
              <Select
                required
                disabled={editing}
                value={form.solution_id ?? ""}
                placeholder="Tria una solució"
                options={solutions.map((item) => ({ value: item.id, label: item.name }))}
                onChange={(event) => setForm((current) => ({ ...current, solution_id: event.target.value, capability_code: "" }))}
              />
            </Field>
            <Field label="Capacitat" required>
              <Select
                required
                disabled={editing}
                value={form.capability_code ?? ""}
                placeholder="Tria una capacitat"
                options={capabilityOptions}
                onChange={(event) => setForm((current) => ({ ...current, capability_code: event.target.value, value_number: "", value_level: "" }))}
              />
            </Field>
            <Field label="La solució la suporta?" required>
              <Select value={form.status ?? "supported"} options={optionsFrom(CAPABILITY_STATUS_LABELS)} onChange={set("status")} />
            </Field>
            {capability?.value_type === "number" && (
              <Field label={`Valor${capability.unit ? ` (${capability.unit})` : ""}`} hint="Sense valor, consta com a suportada però no es pot comparar.">
                <Input type="number" min={0} step="any" value={form.value_number ?? ""} onChange={set("value_number")} />
              </Field>
            )}
            {capability?.value_type === "ordinal" && (
              <Field label="Nivell">
                <Select
                  value={form.value_level ?? ""}
                  placeholder="Sense especificar"
                  options={(capability.ordinal_scale ?? []).map((level) => ({ value: level, label: level }))}
                  onChange={set("value_level")}
                />
              </Field>
            )}
            {form.status === "supported_with_limits" && (
              <Field label="Quins límits té">
                <Textarea rows={2} value={form.limits_note ?? ""} onChange={set("limits_note")} />
              </Field>
            )}
          </>
        )}

        {target.kind === "product" && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Fabricant" required>
                <Input required value={form.manufacturer ?? ""} onChange={set("manufacturer")} />
              </Field>
              <Field label="Model" required>
                <Input required value={form.model ?? ""} onChange={set("model")} />
              </Field>
            </div>
            <Field label="Línia de servei">
              <Combobox value={form.service_line_code ?? ""} placeholder="Sense especificar" options={lineOptions} onChange={setValue("service_line_code")} />
            </Field>
          </>
        )}

        {target.kind === "partner" && (
          <>
            <Field label="Nom" required error={fieldErrors.name}>
              <Input required value={form.name ?? ""} onChange={set("name")} />
            </Field>
            <Field label="NIF">
              <Input value={form.tax_id ?? ""} maxLength={32} onChange={set("tax_id")} />
            </Field>
            {!editing && (
              <fieldset className="space-y-4 rounded-card border border-border px-4 py-3">
                <legend className="px-1 text-[13px] font-medium text-ink">Quin paper fa (opcional)</legend>
                <Field label="Paper">
                  <Select value={form.role ?? ""} placeholder="Sense especificar" options={optionsFrom(PARTNER_ROLE_LABELS)} onChange={set("role")} />
                </Field>
                {form.role && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Línia de servei">
                      <Combobox value={form.role_service_line_code ?? ""} placeholder="Totes" options={lineOptions} onChange={setValue("role_service_line_code")} />
                    </Field>
                    <Field label="Territori">
                      <Input value={form.role_territory ?? ""} onChange={set("role_territory")} />
                    </Field>
                  </div>
                )}
              </fieldset>
            )}
          </>
        )}

        {target.kind === "partner_role" && (
          <>
            <Field label="Paper" required>
              <Select required value={form.role ?? ""} placeholder="Tria un paper" options={optionsFrom(PARTNER_ROLE_LABELS)} onChange={set("role")} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Línia de servei">
                <Combobox value={form.service_line_code ?? ""} placeholder="Totes" options={lineOptions} onChange={setValue("service_line_code")} />
              </Field>
              <Field label="Territori">
                <Input value={form.territory ?? ""} onChange={set("territory")} />
              </Field>
            </div>
          </>
        )}

        {editing && !canDelete && (
          <p className="text-caption text-muted">
            Eliminar-la definitivament és cosa de propietari o administració. La pots retirar amb «Situació»: deixa de
            comptar, però no s&apos;esborra.
          </p>
        )}

        {declarable && (
          <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
            <Field label="Situació" hint="Només el que és vigent compta.">
              <Select value={form.lifecycle ?? "current"} options={optionsFrom(LIFECYCLE_LABELS)} onChange={set("lifecycle")} />
            </Field>
            <Field label="Vàlid fins" error={fieldErrors.valid_until}>
              <Input type="date" value={form.valid_until ?? ""} onChange={set("valid_until")} />
            </Field>
          </div>
        )}
      </form>
    </Modal>
  );
}
