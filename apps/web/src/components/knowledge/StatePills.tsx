import { StatusPill } from "@/components/ui";
import { FLAG_LABELS, STATE_LABELS, STATE_LEVEL, type EffectiveState } from "@/lib/knowledge";

/**
 * L'estat efectiu d'una dada de coneixement: un rètol principal (Verificat,
 * Declarat, Històric detectat…) i, al costat, el que cal saber-ne. Sempre
 * amb paraules: mai el codi intern de la fotografia.
 *
 * «Declarat» és un estat vàlid (addendum 3B-2, B2): no s'hi penja cap pastilla
 * «Falta evidència», perquè l'evidència només es demana quan una licitació la
 * requereix, i tampoc «Pendent de verificar»: un ítem declarat es mostra
 * «Declarat» amb l'acció «Verifica». Sí que es diu «Abast desconegut» (una dada sense abast no cobreix res).
 */
export function StatePills({ value, label }: { value: EffectiveState; label?: string }) {
  const flags = value.flags.filter((flag) => flag !== "evidence_missing" && flag !== "to_verify");
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-state={value.state}>
      <StatusPill level={STATE_LEVEL[value.state]} size="sm">
        {label ?? STATE_LABELS[value.state]}
      </StatusPill>
      {flags.map((flag) => (
        <StatusPill key={flag} level="attention" size="sm" outline>
          {FLAG_LABELS[flag]}
        </StatusPill>
      ))}
    </span>
  );
}
