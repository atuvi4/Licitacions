import { EQUIPMENT_TYPE_LABELS, PROFILE_LABELS, type Vocabulary } from "@/lib/knowledge";

/** Rètol d'un perfil: el local (amb accents) o el del vocabulari de l'API, mai el codi sol. */
export function profileLabel(code: string, vocabulary: Vocabulary | null): string {
  return PROFILE_LABELS[code] ?? vocabulary?.professional_profiles?.find((item) => item.code === code)?.label ?? code;
}

export function equipmentTypeLabel(code: string, vocabulary: Vocabulary | null): string {
  return EQUIPMENT_TYPE_LABELS[code] ?? vocabulary?.equipment_types?.find((item) => item.code === code)?.label ?? code;
}

/** Els perfils que es poden triar: els del vocabulari de l'API si hi és; si no, els coneguts. */
export function profileOptions(vocabulary: Vocabulary | null) {
  const codes = vocabulary?.professional_profiles?.map((item) => item.code) ?? Object.keys(PROFILE_LABELS);
  return codes.map((code) => ({ value: code, label: profileLabel(code, vocabulary) }));
}

export function equipmentTypeOptions(vocabulary: Vocabulary | null) {
  const codes = vocabulary?.equipment_types?.map((item) => item.code) ?? Object.keys(EQUIPMENT_TYPE_LABELS);
  return codes.map((code) => ({ value: code, label: equipmentTypeLabel(code, vocabulary) }));
}
