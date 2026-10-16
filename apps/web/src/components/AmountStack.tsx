/**
 * Un import amb la seva base (P2-06): la xifra principal (VEC → PBL sense IVA → PBL amb
 * IVA, el mateix ordre que el filtre i l'ordenació) i, a sota, el PBL quan és diferent del
 * VEC. Amb cèntims: el PBL és l'import màxim de l'oferta i arrodonir-lo en canviaria el sentit.
 */

import { formatAmount } from "@/lib/format";
import { AMOUNT_BASIS_LABEL, AMOUNT_BASIS_SHORT, AMOUNT_NOT_PUBLISHED, type AmountBasis, tenderAmounts } from "@/lib/tenders";

/** L'abreviatura visible («VEC») i el nom sencer per als lectors de pantalla. */
export function AmountBasisTag({ basis }: { basis: AmountBasis }) {
  return (
    <span className="text-caption text-muted">
      <abbr title={AMOUNT_BASIS_LABEL[basis]} className="no-underline" aria-hidden="true">
        {AMOUNT_BASIS_SHORT[basis]}
      </abbr>
      <span className="sr-only">{AMOUNT_BASIS_LABEL[basis]}</span>
    </span>
  );
}

export function AmountStack({ item, currency }: { item: Parameters<typeof tenderAmounts>[0]; currency: string }) {
  const amounts = tenderAmounts(item);
  if (!amounts.primary) return <span className="text-muted">{AMOUNT_NOT_PUBLISHED}</span>;
  return (
    <span className="inline-flex flex-col items-end">
      <span className="whitespace-nowrap tabular-nums">
        {formatAmount(amounts.primary.value, currency)} <AmountBasisTag basis={amounts.primary.basis} />
      </span>
      {amounts.budget && (
        <span className="whitespace-nowrap text-caption text-muted tabular-nums" data-amount-budget>
          <AmountBasisTag basis={amounts.budget.basis} /> {formatAmount(amounts.budget.value, currency)}
        </span>
      )}
    </span>
  );
}
