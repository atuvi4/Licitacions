import { describe, expect, it } from "vitest";

import type { CompletenessSection } from "@/lib/types";
import type { WorkspaceSummary } from "@/lib/workspace";

import { countPendingDecisions, openOpportunities, profileFooterLine } from "./counts";

function workspace(overrides: Partial<WorkspaceSummary>): WorkspaceSummary {
  return {
    id: "w",
    company_id: "c",
    tender_id: "t",
    tender_lot_id: null,
    status: "awaiting_decision",
    tender_title: null,
    expediente_number: null,
    buyer_name: null,
    deadline_at: null,
    days_remaining: null,
    deadline_passed: false,
    prefit_score: null,
    eligibility: null,
    recommendation: null,
    human_decision: null,
    analysis_basis: "real",
    analysis_outdated: false,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function section(status: CompletenessSection["status"]): CompletenessSection {
  return { key: status, label: status, weight: 1, ratio: 1, points: 1, status, detail: "", requires_evidence: false };
}

describe("countPendingDecisions", () => {
  it("compta les candidatures sense decisió o ajornades que encara estan obertes", () => {
    const rows = [
      workspace({ id: "1", human_decision: null }),
      workspace({ id: "2", human_decision: "defer", status: "awaiting_decision" }),
      workspace({ id: "3", human_decision: "go", status: "preparing_bid" }),
      workspace({ id: "4", human_decision: null, status: "withdrawn" }),
      workspace({ id: "5", human_decision: null, status: "submitted" }),
      workspace({ id: "6", human_decision: "no_go", status: "no_go_approved" }),
    ];
    expect(countPendingDecisions(rows)).toBe(2);
    expect(countPendingDecisions([])).toBe(0);
  });
});

describe("profileFooterLine", () => {
  it("no diu res quan tot és al dia i descriu el que falta quan no", () => {
    expect(profileFooterLine([section("complete"), section("complete")])).toBeNull();
    expect(profileFooterLine([section("complete"), section("partial")])).toBe("1 àrea del perfil és parcial");
    expect(profileFooterLine([section("empty")])).toBe("1 àrea del perfil és buida");
    expect(profileFooterLine([section("empty"), section("partial"), section("complete")])).toBe(
      "2 àrees del perfil no estan al dia",
    );
  });
});

describe("openOpportunities", () => {
  const base = {
    total_tenders: 9, actionable: 0, candidates: 0, high_fit: 0, low_fit: 0, excluded: 0,
    closed: 0, insufficient_data: 0, stale: 0, rules_version: "v1",
  };

  it("sense cap pre-fit calculat no dona xifra: no diu «0 oportunitats obertes»", () => {
    expect(openOpportunities({ ...base, last_calculated_at: null })).toBeUndefined();
  });

  it("amb el pre-fit calculat, les candidates (també quan són zero)", () => {
    expect(openOpportunities({ ...base, candidates: 3, last_calculated_at: "2026-09-29T08:00:00Z" })).toBe(3);
    expect(openOpportunities({ ...base, candidates: 0, last_calculated_at: "2026-09-29T08:00:00Z" })).toBe(0);
  });
});
