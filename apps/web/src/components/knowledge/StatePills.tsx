import { StatusPill } from "@/components/ui";
import { FLAG_LABELS, STATE_LABELS, STATE_LEVEL, type EffectiveState } from "@/lib/knowledge";

/**
 * L'estat efectiu d'una dada de coneixement: un rètol principal (Verificat,
 * Declarat, Històric detectat…) i, al costat, el que encara hi falta. Sempre
 * amb paraules: mai el codi intern de la fotografia.
 */
export function StatePills({ value, label }: { value: EffectiveState; label?: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-state={value.state}>
      <StatusPill level={STATE_LEVEL[value.state]} size="sm">
        {label ?? STATE_LABELS[value.state]}
      </StatusPill>
      {value.flags.map((flag) => (
        <StatusPill key={flag} level="attention" size="sm" outline>
          {FLAG_LABELS[flag]}
        </StatusPill>
      ))}
    </span>
  );
}
