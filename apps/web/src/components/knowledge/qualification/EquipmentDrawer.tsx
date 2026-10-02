"use client";

import { useState } from "react";

import { useCompanyPath } from "@/components/SessionProvider";
import { Checkbox, Combobox, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { EQUIPMENT_ACCESS_LABELS, type EquipmentRecord, type Vocabulary } from "@/lib/knowledge";
import { formatDay, optionsFrom } from "@/lib/format";

import { dayOrNull, errorText, fieldError, parseCount, textOrNull } from "./forms";
import { equipmentTypeOptions } from "./labels";
import { activeLifecycle } from "./PersonDrawer";
import { AsOfField, FormDrawer, Row } from "./shared";
import type { DrawerProps } from "./types";

interface Props extends DrawerProps<EquipmentRecord> {
  vocabulary: Vocabulary | null;
}

const CALIBRATION_OPTIONS = [
  { value: "yes", label: "Sí, requereix calibratge" },
  { value: "no", label: "No en requereix" },
];

function calibrationValue(value: boolean | null | undefined): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "";
}

/**
 * Un equip o instrument que l'empresa pot adscriure a un contracte (OTDR,
 * fusionadora…). No és el catàleg de productes que ven.
 */
export function EquipmentDrawer({ record, vocabulary, verified, canDelete, onClose, onSaved }: Props) {
  const path = useCompanyPath();
  const { saving, error, fieldErrors, run } = useMutation();
  const [type, setType] = useState(record?.equipment_type ?? "");
  const [manufacturer, setManufacturer] = useState(record?.manufacturer ?? "");
  const [model, setModel] = useState(record?.model ?? "");
  const [serial, setSerial] = useState(record?.serial_reference ?? "");
  const [access, setAccess] = useState(record?.access_mode ?? "");
  const [quantity, setQuantity] = useState(String(record?.quantity ?? 1));
  const [active, setActive] = useState(record?.lifecycle !== "retired");
  const [calibration, setCalibration] = useState(calibrationValue(record?.calibration_required));
  const [calibratedUntil, setCalibratedUntil] = useState(record?.calibration_valid_until ?? "");
  // Mai per defecte. Un equip sempre en porta una a l'API: buit vol dir «no la canviïs».
  const [asOf, setAsOf] = useState("");
  const [notes, setNotes] = useState(record?.notes ?? "");
  const [local, setLocal] = useState<Record<string, string>>({});

  function submit() {
    const errors: Record<string, string> = {};
    if (!type) errors.equipment_type = "Tria el tipus d'equip.";
    const units = parseCount(quantity);
    if (units === null || units === undefined || units < 1) errors.quantity = "Indica quantes unitats (un nombre enter, 1 o més).";
    if (calibratedUntil && calibration !== "yes") errors.calibration_valid_until = "Una data de calibratge només té sentit si l'equip en requereix.";
    setLocal(errors);
    if (Object.keys(errors).length > 0) return;
    const lifecycle = record ? activeLifecycle(active, record.lifecycle) : active ? undefined : "retired";
    const body: Record<string, unknown> = {
      equipment_type: type,
      manufacturer: textOrNull(manufacturer),
      model: textOrNull(model),
      serial_reference: textOrNull(serial),
      access_mode: access || null,
      quantity: units,
      calibration_required: calibration === "" ? null : calibration === "yes",
      calibration_valid_until: calibration === "yes" ? dayOrNull(calibratedUntil) : null,
      notes: textOrNull(notes),
      ...(lifecycle ? { lifecycle } : {}),
    };
    // La data de vigència només s'envia si s'ha escrit: en blanc, no se'n diu res.
    const day = dayOrNull(asOf);
    if (day) body.as_of = day;
    void run(async () => {
      if (record) await api.patch(path(`/knowledge/records/equipment/${record.id}`), body);
      else await api.post(path("/knowledge/records/equipment"), body);
      onSaved();
      onClose();
    });
  }

  function remove() {
    if (!record) return;
    void run(async () => {
      await api.delete(path(`/knowledge/records/equipment/${record.id}`));
      onSaved();
      onClose();
    });
  }

  return (
    <FormDrawer
      id="equipment-form"
      title={record ? "Edita l'equip" : "Afegeix un equip"}
      editing={Boolean(record)}
      verified={verified}
      canDelete={canDelete}
      saving={saving}
      error={errorText(error, fieldErrors, ["equipment_type", "manufacturer", "model", "serial_reference", "access_mode", "quantity", "calibration_valid_until", "as_of"])}
      onSubmit={submit}
      onDelete={remove}
      onClose={onClose}
    >
      <Field label="Tipus d'equip" required error={local.equipment_type ?? fieldError(fieldErrors, "equipment_type")}>
        <Combobox id="equipment-type" value={type} options={equipmentTypeOptions(vocabulary)} placeholder="Cerca el tipus..." onChange={setType} />
      </Field>
      <Row>
        <Field label="Fabricant" error={fieldError(fieldErrors, "manufacturer")}>
          <Input value={manufacturer} onChange={(event) => setManufacturer(event.target.value)} />
        </Field>
        <Field label="Model" error={fieldError(fieldErrors, "model")}>
          <Input value={model} onChange={(event) => setModel(event.target.value)} />
        </Field>
      </Row>
      <Row>
        <Field label="Número de sèrie" hint="Opcional." error={fieldError(fieldErrors, "serial_reference")}>
          <Input value={serial} autoComplete="off" onChange={(event) => setSerial(event.target.value)} />
        </Field>
        <Field label="Com se'n disposa" error={fieldError(fieldErrors, "access_mode")}>
          <Select value={access} placeholder="No consta" options={optionsFrom(EQUIPMENT_ACCESS_LABELS)} onChange={(event) => setAccess(event.target.value)} />
        </Field>
      </Row>
      <Row>
        <Field label="Unitats" error={local.quantity ?? fieldError(fieldErrors, "quantity")}>
          <Input inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
        </Field>
        <div className="flex items-end pb-2">
          <Checkbox label="Actiu (disponible a l'empresa)" checked={active} onChange={(event) => setActive(event.target.checked)} />
        </div>
      </Row>
      <Row>
        <Field label="Calibratge" error={fieldError(fieldErrors, "calibration_required")}>
          <Select value={calibration} placeholder="No consta" options={CALIBRATION_OPTIONS} onChange={(event) => setCalibration(event.target.value)} />
        </Field>
        <Field
          label="Calibratge vàlid fins al"
          hint={calibration === "yes" ? undefined : "Només si l'equip requereix calibratge."}
          error={local.calibration_valid_until ?? fieldError(fieldErrors, "calibration_valid_until")}
        >
          <Input type="date" disabled={calibration !== "yes"} value={calibratedUntil} onChange={(event) => setCalibratedUntil(event.target.value)} />
        </Field>
      </Row>
      <AsOfField
        value={asOf}
        onChange={setAsOf}
        error={fieldError(fieldErrors, "as_of")}
        hint={
          record
            ? `Ara consta el ${formatDay(record.as_of)}. Deixa-ho en blanc per no canviar-la.`
            : "Opcional. Si no indiques cap data, Tuvi considerarà la data d'alta."
        }
      />
      <Field label="Notes">
        <Textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
    </FormDrawer>
  );
}
