"""Per que ha fallat: el model, l'esquema, o el text que li vam donar.

Sense aquesta distincio, tot error del benchmark es llegeix igual i la conclusio
sempre acaba sent "el model no ho ha trobat". Al corpus real aixo ja seria fals
almenys una vegada: al plec de Madrid, la formula del criteri economic es una
imatge, i cap model pot extreure el que el text no conte. Comptar-ho com un
error d'extraccio faria que el benchmark mesures una cosa que no es la que diu
mesurar.

Les tres causes son diferents i porten a decisions diferents:

* `MODEL_ERROR` — el text ho deia i el model no ho ha vist, o ho ha vist
  malament. La resposta es un prompt millor o un model millor.
* `REPRESENTATION_LOSS` — el model ho ha trobat pero l'esquema no ho sap
  guardar. La resposta es canviar l'esquema, i aixo es el que va motivar la v2.
* `SOURCE_TEXT_QUALITY` — al text que li vam donar no hi era. La resposta es
  OCR, un parser millor, o assumir que aquell cas no es mesurable.

La tercera no s'ha de "millorar" tocant el model, i confondre-la amb la primera
faria perdre el temps exactament alli on no hi ha res a guanyar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.enums import StrEnum
from app.services.extraction_qa import IssueCode, QaStatus


class ErrorCause(StrEnum):
    MODEL_ERROR = "MODEL_ERROR"
    REPRESENTATION_LOSS = "REPRESENTATION_LOSS"
    SOURCE_TEXT_QUALITY = "SOURCE_TEXT_QUALITY"
    #: No es pot dir. Es un valor legitim: atribuir una causa sense evidencia
    #: seria la mateixa mena d'invencio que el gold evita.
    UNDETERMINED = "UNDETERMINED"


#: Issues de QA que expliquen que una cosa no aparegui al text. La resta
#: —una pagina en blanc al final, una densitat baixa— no expliquen una
#: extraccio fallida i no s'hi han de fer servir com a excusa.
EXPLAINING_ISSUES = frozenset(
    {
        IssueCode.POSSIBLE_MISSING_FORMULA,
        IssueCode.POSSIBLE_MISSING_TABLE,
        IssueCode.IMAGE_ONLY_PAGE,
        IssueCode.EMPTY_PAGE,
        IssueCode.UNREADABLE_CHARACTERS,
        IssueCode.PARSER_WARNING,
    }
)


@dataclass(frozen=True, slots=True)
class Attribution:
    cause: ErrorCause
    #: El codi concret que ho sosté, quan n'hi ha. `POSSIBLE_MISSING_FORMULA`.
    evidence_code: str | None = None
    page_number: int | None = None
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause.value,
            "evidence_code": self.evidence_code,
            "page_number": self.page_number,
            **(self.detail or {}),
        }


def attribute(
    *,
    gold_page: int | None,
    source_issues: list[dict[str, Any]] | None = None,
    representation_unsupported: str | None = None,
    document_name: str | None = None,
    source_quality_status: str | None = None,
) -> Attribution:
    """A que s'atribueix que aquesta anotacio no s'hagi extret be.

    L'ordre no es arbitrari: **primer es mira la font**. Si al text no hi era,
    no te sentit preguntar-se si el model ho va entendre, i encara menys si
    l'esquema ho sabia guardar.

    La coincidencia es per pagina: un avis de formula a la pagina 70 explica un
    criteri d'adjudicacio anotat a la pagina 70, i no un requisit de solvencia
    de la 27. Sense aquesta comprovacio, qualsevol document amb un sol avis
    justificaria tots els seus errors.

    I amb `source_quality_status="unknown"` no s'arriba mai a `MODEL_ERROR`:
    culpar el model quan ningu ha comprovat si el text estava sencer es
    exactament la conclusio que aquesta taxonomia existeix per evitar. En aquest
    cas la resposta honesta es `UNDETERMINED`, i s'hi arriba executant la QA, no
    canviant el model.
    """
    for issue in source_issues or []:
        if issue.get("code") not in {code.value for code in EXPLAINING_ISSUES}:
            continue
        if document_name and issue.get("document_name") not in (None, document_name):
            continue
        page = issue.get("page_number")
        if gold_page is not None and page is not None and page != gold_page:
            continue
        return Attribution(
            cause=ErrorCause.SOURCE_TEXT_QUALITY,
            evidence_code=issue["code"],
            page_number=page,
        )

    if representation_unsupported:
        # Un limit del nostre esquema es demostrable amb el que hem desat, i no
        # depen de si la font s'ha avaluat: el model ho va trobar i nosaltres no
        # ho sabiem guardar.
        return Attribution(
            cause=ErrorCause.REPRESENTATION_LOSS,
            evidence_code=representation_unsupported,
            page_number=gold_page,
        )

    if source_quality_status == QaStatus.UNKNOWN.value:
        return Attribution(
            cause=ErrorCause.UNDETERMINED,
            evidence_code="SOURCE_QUALITY_NOT_EVALUATED",
            page_number=gold_page,
            detail={"hint": "executa la QA d'extraccio sobre les fonts d'aquest run"},
        )
    return Attribution(cause=ErrorCause.MODEL_ERROR, page_number=gold_page)


def summarise_causes(attributions: list[Attribution]) -> dict[str, int]:
    """Recompte per causa, amb totes les claus sempre presents.

    Que hi siguin totes encara que valguin zero es deliberat: un report on la
    fila desapareix quan no hi ha casos fa pensar que la categoria no existeix.
    """
    counts = dict.fromkeys((cause.value for cause in ErrorCause), 0)
    for attribution in attributions:
        counts[attribution.cause.value] += 1
    return counts
