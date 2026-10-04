"""Exporta el vocabulari del motor d'analisi (0051/0052) al web.

    apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py

Escriu `apps/web/src/lib/analysis-vocabulary.generated.json`: determinista (ordenat,
sense dates). `explanations` porta el text canonic de `EXPLANATIONS_BY_VERSION` per
`rules_version` (la UI el mostra tal com es, amb la taula de la versio de cada run) i
`rules_version` es la versio vigent. `rendered` son els valors que la UI pinta amb copy
propi. `contract` son valors que l'API retorna pero la UI v1 no pinta com a text.
No l'editeu a ma: el test `test_analysis_vocabulary_export.py` el compara amb aquesta sortida.
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.registry import (  # noqa: E402
    EXPLANATIONS_BY_VERSION,
    QUESTION_CODES,
    RULE_CODES,
    RULES_VERSION,
)
from app.models.enums import (  # noqa: E402
    AnalysisKnowledgeSource,
    AssessmentVerification,
    CandidateOrigin,
    CandidateReadiness,
    CandidateType,
    CriterionResult,
    FindingType,
    FitResult,
    PointsStatus,
    ProofStatus,
    TerritoryVerdict,
)

TARGET = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "lib"
    / "analysis-vocabulary.generated.json"
)

RENDERED: dict[str, type[StrEnum]] = {
    "analysis_knowledge_source": AnalysisKnowledgeSource,
    "assessment_verification": AssessmentVerification,
    "candidate_origin": CandidateOrigin,
    "candidate_readiness": CandidateReadiness,
    "candidate_type": CandidateType,
    "criterion_result": CriterionResult,
    "finding_type": FindingType,
    "fit_result": FitResult,
    "points_status": PointsStatus,
    "proof_status": ProofStatus,
    "territory_verdict": TerritoryVerdict,
}


def build_vocabulary() -> dict:
    return {
        "contract": {"question_code": sorted(QUESTION_CODES), "rule_code": sorted(RULE_CODES)},
        "explanations": {
            version: {code: table[code] for code in sorted(table)}
            for version, table in sorted(EXPLANATIONS_BY_VERSION.items())
        },
        "rendered": {
            name: sorted(m.value for m in enum) for name, enum in sorted(RENDERED.items())
        },
        "rules_version": RULES_VERSION,
    }


def render(vocabulary: dict) -> str:
    return json.dumps(vocabulary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    TARGET.write_text(render(build_vocabulary()), encoding="utf-8", newline="\n")
    print(f"escrit {TARGET}")


if __name__ == "__main__":
    main()
