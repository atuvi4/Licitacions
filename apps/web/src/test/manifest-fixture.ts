import { MANIFEST_VERSION_V1, MANIFEST_VERSION_V2, type Manifest } from "@/lib/submission";

/** Un manifest v2 sencer, amb documents, solució tècnica i oferta. */
export function manifestV2(overrides: Partial<Manifest> = {}): Manifest {
  return {
    manifest_version: MANIFEST_VERSION_V2,
    revision_number: 1,
    workspace_id: "w1",
    company_id: "c1",
    tender: { tender_id: "t1", tender_lot_id: "tl2", expediente_number: "DEMO-2026-001", deadline_at: "2026-09-21T14:00:00Z" },
    approval_basis: {
      analysis: { requirement_run_id: "run1", compliance_run_id: "crun1", prefit_score: 81 },
      dossier_relevance: [{ evidence_item_id: "ev1" }, { evidence_item_id: "ev2" }],
      technical_scope_fingerprint: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
      technical_validations: [
        {
          solution_id: "s1",
          source: "requirement",
          requirement_id: "r1",
          status: "validated",
          validated_requirement_fingerprint: "req-fp",
          validated_content_fingerprint: "cnt-fp",
        },
      ],
    },
    content: {
      coverage: { mandatory_total: 24, mandatory_covered: 24, document_count: 2 },
      documents: [
        {
          position: 0,
          document_id: "d1",
          filename: "comptes-2025.pdf",
          sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
          file_size: 12345,
          mime_type: "application/pdf",
          category: "economic_solvency",
          requirement_id: "r9",
          requirement_title: "Comptes anuals",
          expires_at: null,
        },
      ],
      technical: [
        {
          solution_id: "s1",
          requirement_id: "r1",
          title: "Commutació d'accés amb redundància",
          proposed_solution: "Es desplegaran 14 commutadors.",
          supplier_name: "Aruba",
          product_name: "CX 6300M",
        },
      ],
      economic: {
        currency: "EUR",
        amounts: { revenue: "127240.00", vat_amount: "26720.40", total_with_vat: "153960.40" },
        lots: [
          {
            lot_id: "lot1",
            tender_lot_id: "tl2",
            label: "Lot 2 · Xarxa d'accés",
            position: 0,
            duration_months: 12,
            amounts: { revenue: "127240.00", vat_amount: "26720.40", total_with_vat: "153960.40" },
            sections: [
              {
                section_id: "sec1",
                title: "Equipament",
                position: 0,
                amounts: { revenue: "11100.00", vat_amount: "2331.00", total_with_vat: "13431.00" },
                lines: [
                  {
                    line_id: "ln1",
                    position: 0,
                    concept: "Commutadors d'accés 48p",
                    quantity: "6",
                    pricing_basis: "per_unit",
                    duration_months_override: null,
                    effective_duration_months: null,
                    periods: "1",
                    offered_unit_price_ex_vat: "1850.0000",
                    vat_rate: "21.00",
                    supplier_name: "Aruba",
                    product_name: "CX 6300M",
                    requirement_id: null,
                    technical_solution_id: "s1",
                    amounts: { revenue: "11100.00", vat_amount: "2331.00", total_with_vat: "13431.00" },
                  },
                ],
              },
            ],
          },
        ],
      },
    },
    ...overrides,
  };
}

/** Un manifest del format anterior: només documents, sense tècnica ni oferta. */
export function manifestV1(): Manifest {
  return {
    manifest_version: MANIFEST_VERSION_V1,
    revision_number: 1,
    workspace_id: "w1",
    company_id: "c1",
    content: {
      coverage: { mandatory_total: 24, mandatory_covered: 24, document_count: 1 },
      documents: manifestV2().content!.documents,
    },
  };
}
