import type { EconomicOffer, InternalTotals, LineInternal, OfferLine, OfferLot, OfferSection, Totals } from "@/lib/economic";

export function totalsFixture(overrides: Partial<Totals> = {}): Totals {
  return {
    total_revenue: "11100.00",
    vat_amount: "2331.00",
    total_with_vat: "13431.00",
    line_count: 1,
    incomplete_revenue_lines: 0,
    incomplete_vat_lines: 0,
    ...overrides,
  };
}

export function internalFixture(overrides: Partial<InternalTotals> = {}): InternalTotals {
  return {
    total_cost: "9120.00",
    margin_amount: "1980.00",
    margin_on_sales_pct: "17.8400",
    markup_on_cost_pct: "21.7100",
    incomplete_cost_lines: 0,
    ...overrides,
  };
}

export function lineInternalFixture(overrides: Partial<LineInternal> = {}): LineInternal {
  return { ...internalFixture(), unit_cost_ex_vat: "1520.0000", monthly_equivalent_cost: null, ...overrides };
}

/** Una línia per unitat, amb preu, IVA i màxim del plec. */
export function lineFixture(overrides: Partial<OfferLine> = {}): OfferLine {
  return {
    id: "ln1",
    section_id: "sec1",
    concept: "Commutadors d'accés 48p",
    requirement_id: null,
    technical_solution_id: null,
    supplier_name: "Aruba",
    product_name: "CX 6300M",
    quantity: "6",
    pricing_basis: "per_unit",
    duration_months_override: null,
    offered_unit_price_ex_vat: "1850.0000",
    tender_max_unit_price_ex_vat: "1900.0000",
    vat_rate: "21.00",
    notes: null,
    position: 0,
    archived_at: null,
    created_at: "2026-09-12T09:00:00Z",
    updated_at: "2026-09-12T09:00:00Z",
    effective_duration_months: null,
    periods: "1",
    monthly_equivalent_revenue: null,
    exceeds_unit_max: false,
    totals: totalsFixture(),
    internal_economics: lineInternalFixture(),
    ...overrides,
  };
}

export function sectionFixture(overrides: Partial<OfferSection> = {}): OfferSection {
  return {
    id: "sec1",
    offer_lot_id: "lot1",
    title: "Equipament",
    position: 0,
    archived_at: null,
    totals: totalsFixture(),
    internal_economics: internalFixture(),
    lines: [lineFixture()],
    ...overrides,
  };
}

export function lotFixture(overrides: Partial<OfferLot> = {}): OfferLot {
  return {
    id: "lot1",
    tender_lot_id: "tl2",
    label: "Lot 2 · Xarxa d'accés",
    duration_months: 12,
    position: 0,
    archived_at: null,
    totals: totalsFixture(),
    internal_economics: internalFixture(),
    limit: { tender_max: "240000.00", difference_to_maximum: "-228900.00", exceeds_lot_max: false },
    sections: [sectionFixture()],
    ...overrides,
  };
}

/**
 * Una oferta sencera amb els números de dins visibles. Per als rols redactats,
 * les proves passen `can_view_internal_economics: false` i treuen els blocs,
 * que és exactament el que fa el servidor.
 */
export function offerFixture(overrides: Partial<EconomicOffer> = {}): EconomicOffer {
  return {
    id: "off1",
    workspace_id: "w1",
    currency: "EUR",
    notes: null,
    revision: 4,
    can_view_internal_economics: true,
    can_select_lots: false,
    totals: totalsFixture(),
    internal_economics: internalFixture(),
    lots: [lotFixture()],
    ...overrides,
  };
}

/** La mateixa oferta tal com la retorna el servidor a un rol sense costos. */
export function redactedOffer(offer: EconomicOffer = offerFixture()): EconomicOffer {
  return {
    ...offer,
    can_view_internal_economics: false,
    internal_economics: null,
    lots: offer.lots.map((lot) => ({
      ...lot,
      internal_economics: null,
      sections: lot.sections.map((section) => ({
        ...section,
        internal_economics: null,
        lines: section.lines.map((line) => ({ ...line, internal_economics: null })),
      })),
    })),
  };
}
