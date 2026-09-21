import { describe, expect, it } from "vitest";

import { manifestV1, manifestV2 } from "@/test/manifest-fixture";
import { STALE_REASON_LABELS, staleReasonLabel } from "@/lib/workspace";
import {
  FORBIDDEN_MANIFEST_FIELDS,
  MANIFEST_FORMAT_LABELS,
  approvalDecisionLabel,
  hasFullContent,
  manifestDocuments,
  manifestFormat,
  manifestLines,
  manifestOffer,
  manifestRelevance,
  manifestSolutions,
  manifestValidations,
  pricingBasisLabel,
  readManifest,
} from "@/lib/submission";

describe("Versió del manifest", () => {
  it("distingeix el format actual de l'anterior i del desconegut", () => {
    expect(manifestFormat("submission-manifest-v2")).toBe("current");
    expect(manifestFormat("submission-manifest-v1")).toBe("legacy");
    expect(manifestFormat("submission-manifest-v9")).toBe("unknown");
    expect(manifestFormat(null)).toBe("unknown");
    expect(manifestFormat(undefined)).toBe("unknown");
  });

  it("només el format actual porta contingut tècnic i econòmic", () => {
    expect(hasFullContent(manifestV2())).toBe(true);
    expect(hasFullContent(manifestV1())).toBe(false);
    expect(hasFullContent(null)).toBe(false);
  });

  it("cada format té nom llegible", () => {
    expect(MANIFEST_FORMAT_LABELS.current).toBe("Format actual");
    expect(MANIFEST_FORMAT_LABELS.legacy).toBe("Format anterior");
    expect(MANIFEST_FORMAT_LABELS.unknown).toBe("Format desconegut");
  });
});

describe("Lectura del manifest", () => {
  it("llegeix el contingut real d'un v2", () => {
    const manifest = manifestV2();
    expect(manifestDocuments(manifest)).toHaveLength(1);
    expect(manifestSolutions(manifest)[0].title).toBe("Commutació d'accés amb redundància");
    expect(manifestOffer(manifest)?.currency).toBe("EUR");
    expect(manifestLines(manifestOffer(manifest))).toHaveLength(1);
    expect(manifestValidations(manifest)).toHaveLength(1);
    expect(manifestRelevance(manifest)).toHaveLength(2);
  });

  it("un v1 té documents però ni tècnica ni oferta", () => {
    const manifest = manifestV1();
    expect(manifestDocuments(manifest)).toHaveLength(1);
    expect(manifestSolutions(manifest)).toEqual([]);
    expect(manifestOffer(manifest)).toBeNull();
    expect(manifestLines(manifestOffer(manifest))).toEqual([]);
  });

  it("un manifest inesperat no peta ni s'inventa res", () => {
    expect(readManifest(null)).toBeNull();
    expect(readManifest("una cadena")).toBeNull();
    expect(readManifest([1, 2])).toBeNull();
    const buit = readManifest({});
    expect(buit).not.toBeNull();
    expect(manifestDocuments(buit)).toEqual([]);
    expect(manifestSolutions(buit)).toEqual([]);
    expect(manifestOffer(buit)).toBeNull();
    expect(manifestValidations(buit)).toEqual([]);
  });

  it("un camp que no té la forma esperada es tracta com a absent", () => {
    const trencat = readManifest({ manifest_version: "submission-manifest-v2", content: { documents: "no és una llista" } });
    expect(manifestDocuments(trencat)).toEqual([]);
  });

  it("l'oferta congelada no porta cap xifra interna", () => {
    // La garantia és del backend; això comprova que el nostre snapshot de
    // referència tampoc no en porta, i deixa escrit què no hi pot haver.
    const serialized = JSON.stringify(manifestV2());
    for (const field of FORBIDDEN_MANIFEST_FIELDS) {
      // Com a CLAU, no com a subcadena: `revision` no hi pot ser, però
      // `revision_number` sí, i una comprovació per subcadena els confondria.
      expect(serialized).not.toContain(`"${field}":`);
    }
  });
});

describe("Etiquetes", () => {
  it("la base de preu es tradueix, i una de desconeguda es mostra tal qual", () => {
    expect(pricingBasisLabel("per_unit")).toBe("Per unitat");
    expect(pricingBasisLabel("monthly")).toBe("Mensual");
    expect(pricingBasisLabel("una_altra")).toBe("una_altra");
    expect(pricingBasisLabel(null)).toBe("—");
  });

  it("les dues decisions del contracte tenen nom", () => {
    expect(approvalDecisionLabel("approved_for_submission")).toBe("Aprovada");
    expect(approvalDecisionLabel("rejected")).toBe("Rebutjada");
    expect(approvalDecisionLabel("cosa_nova")).toBe("cosa_nova");
  });

  it("el motiu de format antic té frase pròpia, i no diu que hagi canviat res", () => {
    const label = staleReasonLabel("manifest_version_outdated");
    expect(label).toContain("format anterior");
    expect(label).not.toContain("ha canviat");
  });

  it("el motiu genèric no promet cap diferència camp a camp", () => {
    const label = STALE_REASON_LABELS.manifest_changed;
    expect(label).toContain("ja no coincideix");
    expect(label).not.toMatch(/substituït per|de .* a |abans/);
  });
});
