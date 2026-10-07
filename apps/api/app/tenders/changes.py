"""Que ha canviat d'una licitacio, i a que afecta.

Una licitacio no es una fotografia: l'organ de contractacio en publica
modificacions —allarga el termini, substitueix un plec, canvia un import— i el
sistema n'ha de saber tres coses alhora: **que** ha canviat, **quan** ho hem
sabut, i **que deixa d'estar vigent** per culpa d'aixo.

Modul pur: dos diccionaris entren, una llista de canvis en surt. Ni base de
dades, ni HTTP, ni rellotge. Aixo es el que permet provar la matriu d'impacte
sense muntar mig sistema.

Tres decisions que el governen:

* **Els canvis son estructurats, no un diff de text.** "El JSON ha canviat" no
  serveix per decidir res. `DEADLINE_CHANGED` amb el valor vell i el nou, si.
* **Un document no es identifica per la URL.** El mateix enllaç pot servir un
  fitxer diferent i un enllaç nou pot servir el mateix fitxer. Qui mana es el
  SHA-256 del contingut, quan el coneixem.
* **L'impacte no s'endevina.** Cada codi de canvi te la seva llista, escrita en
  una taula que es pot llegir i discutir. Deduir-lo a base de condicionals
  faria que ningu sabes mai per que un workspace ha quedat obsolet.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models.enums import StrEnum


def canonical_instant(value: datetime | str | None) -> str | None:
    """Un instant, sempre escrit igual: en UTC.

    `2026-11-28T19:00:00+01:00` i `2026-11-28T18:00:00+00:00` son el MATEIX
    moment. Si entren a la fotografia tal com arriben, un canvi de com la font
    —o el driver de la base de dades— escriu l'hora sembla que l'organ ha mogut
    el termini: revisio nova, event `DEADLINE_CHANGED` material i, darrere seu,
    l'avis que tota l'analisi ha quedat endarrerida.

    No es hipotetic: passava a la PRIMERA licitacio que es tocava. En processar
    el primer document la fotografia es tornava a construir, l'hora sortia amb
    un offset diferent, i la candidatura naixia amb un avis d'un canvi que
    ningu havia fet.

    Un datetime sense zona es tracta com a UTC. Es una suposicio, pero es la que
    ja fa la resta del sistema —les columnes son `timezone=True` i tot el que
    s'hi escriu porta zona—; comparar naive amb aware nomes mouria el fals
    positiu de lloc.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            # No es un instant: es retorna tal qual i es compara com a text.
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def same_instant(old: Any, new: Any) -> bool:
    """Cert quan dos valors son el mateix moment, encara que s'escriguin diferent.

    Les fotografies noves ja arriben canonicalitzades, pero les que es van desar
    abans d'aquesta correccio no. Comparar aqui tambe l'instant evita que la
    primera revisio posterior al desplegament generi el fals positiu que
    justament s'esta corregint.
    """
    if old == new:
        return True
    if old is None or new is None:
        return False
    return canonical_instant(old) == canonical_instant(new)

#: Versio de la logica de deteccio. Es desa a cada revisio: si algun dia
#: canviem que compta com a canvi, dues revisions calculades amb criteris
#: diferents no serien comparables.
CHANGE_DETECTION_VERSION = "tender-changes-v1"

#: Camps de la fotografia que son un INSTANT i no una cadena qualsevol. Es
#: comparen amb `same_instant`: el mateix moment escrit amb un offset diferent
#: no es un canvi. Tot camp nou que porti data i hora ha d'entrar aqui.
_INSTANT_FIELDS: frozenset[str] = frozenset({"deadline_at", "published_at"})


def _same_field(name: str, old: Any, new: Any) -> bool:
    """Iguals segons el TIPUS del camp, no segons com estan escrits."""
    if name in _INSTANT_FIELDS:
        return same_instant(old, new)
    return old == new


class ChangeImpact(StrEnum):
    """A que afecta un canvi. Un canvi en pot tenir mes d'un."""

    #: No invalida res. Es historia, i prou.
    INFORMATIONAL = "informational"
    #: L'interes de la licitacio per a l'empresa pot ser un altre.
    PREFIT_AFFECTING = "prefit_affecting"
    #: El que exigeix el plec pot ser un altre: cal tornar a extreure.
    REQUIREMENTS_AFFECTING = "requirements_affecting"
    #: El veredicte de compliment pot ser un altre encara que els requisits
    #: siguin els mateixos: passa quan canvia un import o el termini.
    COMPLIANCE_AFFECTING = "compliance_affecting"
    #: Afecta directament una decisio humana o la capacitat de presentar-se.
    WORKSPACE_CRITICAL = "workspace_critical"


class ChangeCode(StrEnum):
    """Que ha canviat. Codis tancats: la interficie en deriva el text."""

    TITLE_CHANGED = "TITLE_CHANGED"
    DESCRIPTION_CHANGED = "DESCRIPTION_CHANGED"
    BUYER_CHANGED = "BUYER_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PROCEDURE_TYPE_CHANGED = "PROCEDURE_TYPE_CHANGED"
    CONTRACT_TYPE_CHANGED = "CONTRACT_TYPE_CHANGED"
    TENDER_CANCELLED = "TENDER_CANCELLED"
    PUBLICATION_DATE_CHANGED = "PUBLICATION_DATE_CHANGED"
    #: Nomes a les licitacions obertes a ma: a les sindicades el numero
    #: d'expedient i el pais els fixa la font i no els corregeix ningu.
    EXPEDIENTE_NUMBER_CHANGED = "EXPEDIENTE_NUMBER_CHANGED"
    COUNTRY_CHANGED = "COUNTRY_CHANGED"

    DEADLINE_CHANGED = "DEADLINE_CHANGED"
    DEADLINE_ADDED = "DEADLINE_ADDED"
    #: El termini desapareix. No vol dir que no n'hi hagi: vol dir que no el
    #: sabem, i tot el que en depengui deixa de ser calculable.
    DEADLINE_REMOVED = "DEADLINE_REMOVED"

    ESTIMATED_VALUE_CHANGED = "ESTIMATED_VALUE_CHANGED"
    BASE_BUDGET_CHANGED = "BASE_BUDGET_CHANGED"
    CURRENCY_CHANGED = "CURRENCY_CHANGED"
    PLACE_CHANGED = "PLACE_CHANGED"

    CPV_ADDED = "CPV_ADDED"
    CPV_REMOVED = "CPV_REMOVED"
    MAIN_CPV_CHANGED = "MAIN_CPV_CHANGED"

    LOT_ADDED = "LOT_ADDED"
    #: El lot ja no es al feed. La font no publica cap "lot anul.lat"
    #: distingible, aixi que desapareixer ES el senyal, i no s'hi inventa cap
    #: mapatge legal. El lot NO s'esborra de la base de dades: una candidatura
    #: oberta hi apunta, i esborrar-lo se l'enduria.
    LOT_REMOVED = "LOT_REMOVED"
    #: El lot torna a ser al feed despres d'haver-ne desaparegut.
    LOT_RESTORED = "LOT_RESTORED"
    LOT_VALUE_CHANGED = "LOT_VALUE_CHANGED"
    LOT_CPV_CHANGED = "LOT_CPV_CHANGED"
    LOT_TITLE_CHANGED = "LOT_TITLE_CHANGED"
    #: El termini d'un lot mana sobre el de l'expedient per a qui s'hi presenta.
    LOT_DEADLINE_CHANGED = "LOT_DEADLINE_CHANGED"
    #: Numero de lot i companyia: canvia com se'n parla, no que es.
    LOT_METADATA_CHANGED = "LOT_METADATA_CHANGED"

    #: Presencia a la font (tombstone): la font ha retirat l'entrada del feed. NO es un
    #: estat de la licitacio ni vol dir que s'hagi anul.lat: informatiu.
    SOURCE_ENTRY_REMOVED = "SOURCE_ENTRY_REMOVED"
    #: L'entrada torna a ser a la font despres d'un tombstone.
    SOURCE_ENTRY_RESTORED = "SOURCE_ENTRY_RESTORED"

    DOCUMENT_ADDED = "DOCUMENT_ADDED"
    DOCUMENT_REMOVED = "DOCUMENT_REMOVED"
    #: Mateixa URL, contingut nou: una versio real del document.
    DOCUMENT_REPLACED = "DOCUMENT_REPLACED"
    #: URL nova, mateix contingut: ha canviat on es publica, no que diu.
    DOCUMENT_URL_CHANGED = "DOCUMENT_URL_CHANGED"
    #: Nom, tipus o data de publicacio, amb el mateix contingut.
    DOCUMENT_METADATA_CHANGED = "DOCUMENT_METADATA_CHANGED"
    #: Primer cop que en sabem el contingut. NO es una modificacio de la font:
    #: es nomes que abans no l'haviem baixat. Vegeu `_document_changes`.
    DOCUMENT_CONTENT_IDENTIFIED = "DOCUMENT_CONTENT_IDENTIFIED"


#: **La matriu d'impacte.** Escrita, no deduida.
#:
#: El criteri de cada fila:
#:
#: * El **termini** afecta el pre-fit, el compliment i la candidatura. El
#:   pre-fit perque te component de calendari (`timing_score`): si el termini es
#:   mou, la puntuacio calculada contra el termini vell ja no es la que surt
#:   avui, tant si s'ajorna com si s'avança. El compliment perque la vigencia
#:   d'un certificat es mesura contra el termini. I la candidatura perque tot el
#:   que s'estava preparant tenia aquella data.
#: * Un **import** no canvia el que diu el plec, i per tant no invalida els
#:   requisits: un requisit expressat com "el 50% del pressupost base" segueix
#:   dient exactament el mateix. El que canvia es el **resultat** d'avaluar-lo,
#:   perque 4C.2 resol els llindars percentuals contra `estimated_value` i
#:   `base_budget`. Per aixo els imports son compliance_affecting i no
#:   requirements_affecting: reextreure els plecs no cambiaria res, recalcular
#:   el compliment si.
#: * Un **document substituit** ho canvia tot aigues avall: el que exigeix el
#:   plec pot ser un altre.
#: * Una **URL nova amb el mateix contingut** no canvia res material. Tractar-ho
#:   com una substitucio obligaria a reanalitzar i a reobrir decisions sense cap
#:   motiu, i el sistema s'ompliria d'avisos que no volen dir res.
IMPACT_MATRIX: dict[ChangeCode, frozenset[ChangeImpact]] = {
    ChangeCode.TITLE_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.DESCRIPTION_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.BUYER_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.PUBLICATION_DATE_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.STATUS_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    #: Canviar de procediment o de tipus de contracte es una republicacio de
    #: fons: canvia si ens interessa i canvia el regim de solvencia exigible.
    #: No es marca com a "afecta els requisits" perque els requisits surten dels
    #: plecs, i si els plecs han canviat ja ho dira `DOCUMENT_REPLACED`.
    ChangeCode.PROCEDURE_TYPE_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.CONTRACT_TYPE_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.TENDER_CANCELLED: frozenset({ChangeImpact.WORKSPACE_CRITICAL}),

    ChangeCode.DEADLINE_CHANGED: frozenset(
        {
            ChangeImpact.PREFIT_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),
    ChangeCode.DEADLINE_ADDED: frozenset(
        {
            ChangeImpact.PREFIT_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),
    ChangeCode.DEADLINE_REMOVED: frozenset(
        {
            ChangeImpact.PREFIT_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),

    ChangeCode.ESTIMATED_VALUE_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.BASE_BUDGET_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.CURRENCY_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.PLACE_CHANGED: frozenset({ChangeImpact.PREFIT_AFFECTING}),
    #: Corregir com es diu un expedient no canvia que es. Es una etiqueta per a
    #: qui el busca, i cap calcul no la llegeix.
    ChangeCode.EXPEDIENTE_NUMBER_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    #: El pais NO es l'equivalent gros de `nuts_code`: el motor de pre-fit
    #: nomes llegeix `nuts_code` i `place_of_execution`, i marcar-lo com a
    #: `PREFIT_AFFECTING` faria caducar paquets per un camp que ningu calcula.
    ChangeCode.COUNTRY_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),

    ChangeCode.CPV_ADDED: frozenset({ChangeImpact.PREFIT_AFFECTING}),
    ChangeCode.CPV_REMOVED: frozenset({ChangeImpact.PREFIT_AFFECTING}),
    ChangeCode.MAIN_CPV_CHANGED: frozenset({ChangeImpact.PREFIT_AFFECTING}),

    ChangeCode.LOT_ADDED: frozenset({ChangeImpact.PREFIT_AFFECTING}),
    #: Un lot que desapareix pot ser el lot d'un workspace obert.
    ChangeCode.LOT_REMOVED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.WORKSPACE_CRITICAL}
    ),
    #: Un lot que torna no es innocu: una candidatura que apuntava al lot ha de
    #: tornar a mirar-s'ho, perque mentrestant es va donar per no accionable.
    ChangeCode.LOT_RESTORED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.WORKSPACE_CRITICAL}
    ),
    #: L'import d'un lot es base de llindar percentual igual que el de
    #: l'expedient: qui es presenta a un lot es mesura contra el lot.
    ChangeCode.LOT_VALUE_CHANGED: frozenset(
        {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
    ),
    ChangeCode.LOT_CPV_CHANGED: frozenset({ChangeImpact.PREFIT_AFFECTING}),
    ChangeCode.LOT_TITLE_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.LOT_DEADLINE_CHANGED: frozenset(
        {
            ChangeImpact.PREFIT_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),
    ChangeCode.LOT_METADATA_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.SOURCE_ENTRY_REMOVED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.SOURCE_ENTRY_RESTORED: frozenset({ChangeImpact.INFORMATIONAL}),

    #: Un plec NOU diu que hi ha material per llegir, no que el que ja sabiem
    #: hagi canviat. Obliga a tornar a extreure requisits —per aixo es
    #: `requirements_affecting` i per aixo continua sent material—, pero no
    #: afirma res del compliment: el veredicte de compliment es calcula sobre
    #: els requisits, i mentre ningu no hagi llegit el document nou els
    #: requisits vigents son exactament els d'abans. Quan algu el llegeixi i
    #: en surtin requisits diferents, hi haura un run nou i qui en depengui ho
    #: sabra per aquell cami.
    #:
    #: Es diferent de substituir-ne un o de perdre'n un: alla el material que
    #: ja formava part del que s'havia decidit deixa de ser el mateix.
    ChangeCode.DOCUMENT_ADDED: frozenset({ChangeImpact.REQUIREMENTS_AFFECTING}),
    ChangeCode.DOCUMENT_REMOVED: frozenset(
        {
            ChangeImpact.REQUIREMENTS_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),
    ChangeCode.DOCUMENT_REPLACED: frozenset(
        {
            ChangeImpact.REQUIREMENTS_AFFECTING,
            ChangeImpact.COMPLIANCE_AFFECTING,
            ChangeImpact.WORKSPACE_CRITICAL,
        }
    ),
    ChangeCode.DOCUMENT_URL_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    ChangeCode.DOCUMENT_METADATA_CHANGED: frozenset({ChangeImpact.INFORMATIONAL}),
    #: Saber per primer cop que hi ha dins d'un document que ja teniem
    #: catalogat es enriquiment NOSTRE, no una modificacio de l'organ. Marcar-ho
    #: com a substitucio voldria dir reextreure els plecs i reobrir decisions
    #: humanes el dia que ens posem al dia amb les descarregues, i el motiu
    #: seria fals: el document sempre havia sigut aquell.
    ChangeCode.DOCUMENT_CONTENT_IDENTIFIED: frozenset({ChangeImpact.INFORMATIONAL}),
}


@dataclass(frozen=True, slots=True)
class Change:
    code: ChangeCode
    old_value: Any = None
    new_value: Any = None
    #: Que identifica l'element afectat quan no es el propi expedient: el codi
    #: d'un lot, la URL d'un document.
    subject: str | None = None

    @property
    def impacts(self) -> frozenset[ChangeImpact]:
        return IMPACT_MATRIX.get(self.code, frozenset({ChangeImpact.INFORMATIONAL}))

    @property
    def is_material(self) -> bool:
        """Cert si aquest canvi invalida alguna cosa.

        Un canvi purament informatiu no ha de fer saltar cap avis ni reobrir cap
        decisio: si ho fes, el senyal es gastaria i el dia que canvies un plec
        ningu ho miraria.
        """
        return self.impacts != frozenset({ChangeImpact.INFORMATIONAL})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "subject": self.subject,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "impacts": sorted(impact.value for impact in self.impacts),
        }


#: Els impactes que fan que un paquet congelat deixi de ser vigent.
#:
#: NO hi es `REQUIREMENTS_AFFECTING`, i es la distincio que sosté tota la
#: pantalla d'aprovacio: que arribi material per llegir no vol dir que el que
#: s'enviaria hagi canviat. Qui ha de reaccionar a material nou es l'extraccio
#: de requisits (`requirements_outdated`); si en surten requisits diferents,
#: el paquet ho sabra per `requirement_run_changed`, que compara runs i no
#: events. Sense aquesta separacio, qualsevol annex publicat despres de
#: l'aprovacio la faria caducar, i el dia que canviés un plec de veritat ningu
#: no s'ho miraria.
PACKAGE_STALE_IMPACTS: frozenset[ChangeImpact] = frozenset(
    {
        ChangeImpact.PREFIT_AFFECTING,
        ChangeImpact.COMPLIANCE_AFFECTING,
        ChangeImpact.WORKSPACE_CRITICAL,
    }
)


def impacts_of(changes: list[Change]) -> frozenset[ChangeImpact]:
    """Tots els impactes d'un conjunt de canvis."""
    result: set[ChangeImpact] = set()
    for change in changes:
        result |= change.impacts
    return frozenset(result)


def has_material_changes(changes: list[Change]) -> bool:
    return any(change.is_material for change in changes)


# --------------------------------------------------------------------------- #
# Fotografia normalitzada                                                       #
# --------------------------------------------------------------------------- #


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 de la fotografia, amb serialitzacio canonica.

    Es el mecanisme d'idempotencia: reingerir la mateixa entrada dona el mateix
    hash i, per tant, cap revisio nova, cap event i cap avis. Les claus
    s'ordenen perque l'ordre d'un diccionari no ha de canviar la identitat del
    contingut.
    """
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Comparacio                                                                    #
# --------------------------------------------------------------------------- #

#: Camps escalars i el codi que genera canviar-los.
#:
#: **Tot camp escalar de la fotografia ha de ser aqui.** Un camp que entra al
#: hash pero no es compara produiria una revisio nova sense cap canvi que
#: l'expliqui: el sistema diria "ha canviat alguna cosa" sense poder dir que, i
#: aixo es la mena d'avis que la gent apren a ignorar. `COMPARED_FIELDS` i el
#: seu test ho impedeixen.
#:
#: Dos camps poden compartir codi —un pressupost amb IVA i un sense son el
#: mateix fet— i llavors el `subject` diu quin dels dos s'ha mogut.
_SCALAR_FIELDS: tuple[tuple[str, ChangeCode], ...] = (
    ("title", ChangeCode.TITLE_CHANGED),
    ("description", ChangeCode.DESCRIPTION_CHANGED),
    ("buyer", ChangeCode.BUYER_CHANGED),
    ("procedure_type", ChangeCode.PROCEDURE_TYPE_CHANGED),
    ("contract_type", ChangeCode.CONTRACT_TYPE_CHANGED),
    ("publication_date", ChangeCode.PUBLICATION_DATE_CHANGED),
    ("estimated_value", ChangeCode.ESTIMATED_VALUE_CHANGED),
    ("base_budget", ChangeCode.BASE_BUDGET_CHANGED),
    ("base_budget_without_tax", ChangeCode.BASE_BUDGET_CHANGED),
    ("currency", ChangeCode.CURRENCY_CHANGED),
    ("place_of_execution", ChangeCode.PLACE_CHANGED),
    ("nuts_code", ChangeCode.PLACE_CHANGED),
    ("expediente_number", ChangeCode.EXPEDIENTE_NUMBER_CHANGED),
    ("country_code", ChangeCode.COUNTRY_CHANGED),
)

#: Els dos camps que nomes hi ha a la fotografia d'una licitacio oberta a ma.
#:
#: A una de sindicada els fixa la font i ningu no els corregeix, aixi que
#: afegir-los-hi faria semblar canviades totes les que ja hi ha el dia que
#: tornessin a passar per aqui. A una de manual, en canvi, son editables, i un
#: camp editable que no entra a la fotografia deixa forats al CAS: dos clients
#: que llegeixen la mateixa revisio poden escriure tots dos sense saber-ho.
MANUAL_ONLY_FIELDS: frozenset[str] = frozenset({"expediente_number", "country_code"})

#: Presencia a la font (lapides, radar 3B-1). `source_removed` nomes entra a la
#: fotografia quan val `True`: amb l'estat neutre omes, la fotografia de totes
#: les licitacions que ja hi ha no canvia, i el dia del desplegament no apareix
#: cap revisio sense cap canvi que l'expliqui. L'absencia equival a `False`.
SOURCE_PRESENCE_FIELDS: frozenset[str] = frozenset({"source_removed"})

#: Els camps de la fotografia que NO son escalars i els compara un detector
#: propi. Amb `_SCALAR_FIELDS` han de cobrir la fotografia sencera.
_STRUCTURED_FIELDS: frozenset[str] = frozenset(
    {"status", "deadline_at", "cpvs", "lots", "documents"}
) | SOURCE_PRESENCE_FIELDS

#: Tot el que es compara. El test de cobertura hi compara les claus reals.
COMPARED_FIELDS: frozenset[str] = frozenset(
    name for name, _ in _SCALAR_FIELDS
) | _STRUCTURED_FIELDS

#: Estats que volen dir que l'expedient ja no es presentable. Nomes els que la
#: font distingeix de veritat: no s'hi inventa cap mapatge legal.
NOT_ACTIONABLE_STATUSES = frozenset({"cancelled"})


def _deadline_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    old, new = before.get("deadline_at"), after.get("deadline_at")
    # `same_instant` i no `==`: el mateix moment escrit amb un offset diferent
    # no es cap canvi de termini. Veure `canonical_instant`.
    if same_instant(old, new):
        return []
    if old is None:
        return [Change(ChangeCode.DEADLINE_ADDED, None, new)]
    if new is None:
        # Perdre el termini no es innocu: tot el que es mesurava contra ell
        # —vigencia de documents, readiness, coherencia del paquet— deixa de ser
        # calculable, i seguir ensenyant el calcul antic seria mentir.
        return [Change(ChangeCode.DEADLINE_REMOVED, old, None)]
    return [Change(ChangeCode.DEADLINE_CHANGED, old, new)]


def _source_presence_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """La font ha retirat l'entrada (lapida) o la torna a publicar.

    No es un estat contractual: l'estat no es toca i el codi es informatiu.
    """
    was, now = bool(before.get("source_removed")), bool(after.get("source_removed"))
    if was == now:
        return []
    code = ChangeCode.SOURCE_ENTRY_REMOVED if now else ChangeCode.SOURCE_ENTRY_RESTORED
    return [Change(code, was, now, subject="source_removed")]


def _status_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    old, new = before.get("status"), after.get("status")
    if old == new:
        return []
    if new in NOT_ACTIONABLE_STATUSES:
        return [Change(ChangeCode.TENDER_CANCELLED, old, new)]
    return [Change(ChangeCode.STATUS_CHANGED, old, new)]


def _cpv_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    old = {entry["code"]: entry for entry in before.get("cpvs", [])}
    new = {entry["code"]: entry for entry in after.get("cpvs", [])}
    changes = [
        Change(ChangeCode.CPV_ADDED, None, code, subject=code)
        for code in sorted(set(new) - set(old))
    ]
    changes += [
        Change(ChangeCode.CPV_REMOVED, code, None, subject=code)
        for code in sorted(set(old) - set(new))
    ]
    old_main = next((code for code, entry in old.items() if entry.get("is_main")), None)
    new_main = next((code for code, entry in new.items() if entry.get("is_main")), None)
    if old_main != new_main:
        changes.append(Change(ChangeCode.MAIN_CPV_CHANGED, old_main, new_main))
    return changes


#: Camps d'un lot que es comparen un per un. Amb els que tenen detector propi
#: —`removed`— han de cobrir el lot sencer: vegeu `LOT_FIELDS`.
_LOT_FIELD_CODES: tuple[tuple[str, ChangeCode], ...] = (
    ("title", ChangeCode.LOT_TITLE_CHANGED),
    ("lot_number", ChangeCode.LOT_METADATA_CHANGED),
    ("estimated_value", ChangeCode.LOT_VALUE_CHANGED),
    ("base_budget", ChangeCode.LOT_VALUE_CHANGED),
    #: La moneda es part de l'import: 100.000 no vol dir el mateix en dues
    #: monedes diferents, i el llindar percentual es calcula sobre el numero.
    ("currency", ChangeCode.LOT_VALUE_CHANGED),
    ("deadline_at", ChangeCode.LOT_DEADLINE_CHANGED),
    ("cpvs", ChangeCode.LOT_CPV_CHANGED),
)

#: Tot el que un lot aporta a la fotografia. El test de cobertura ho compara amb
#: les claus reals, pel mateix motiu que `COMPARED_FIELDS` a dalt: un camp que
#: entra al hash pero no es compara donaria una revisio sense cap canvi que
#: l'expliqui.
LOT_FIELDS: frozenset[str] = frozenset(
    {"external_lot_id", "removed"} | {name for name, _ in _LOT_FIELD_CODES}
)


def _lot_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """Els canvis d'un lot, inclos el fet de deixar de ser al feed.

    Un lot que desapareix **no s'esborra**: la fila es marca i es queda a la
    fotografia amb `removed: True`. Esborrar-la se'n duria per davant qualsevol
    candidatura que hi apuntes, i el que ha de passar es exactament el contrari:
    que qui hi treballava ho sapiga.
    """
    old = {entry["external_lot_id"]: entry for entry in before.get("lots", [])}
    new = {entry["external_lot_id"]: entry for entry in after.get("lots", [])}
    changes: list[Change] = []

    for key in sorted(set(new) - set(old)):
        changes.append(Change(ChangeCode.LOT_ADDED, None, new[key].get("title"), subject=key))
    for key in sorted(set(old) - set(new)):
        # Nomes pot passar amb dades d'abans de la marca `removed`. Es tracta
        # igual: el lot ja no hi es.
        changes.append(Change(ChangeCode.LOT_REMOVED, old[key].get("title"), None, subject=key))

    for key in sorted(set(old) & set(new)):
        previous, current = old[key], new[key]
        was_removed, is_removed = bool(previous.get("removed")), bool(current.get("removed"))
        if was_removed != is_removed:
            code = ChangeCode.LOT_REMOVED if is_removed else ChangeCode.LOT_RESTORED
            changes.append(Change(code, previous.get("title"), current.get("title"), subject=key))
        if is_removed:
            # D'un lot que ja no hi es no se n'informen els retocs: el fet
            # rellevant es que no hi es, i la resta seria soroll.
            continue
        seen_codes: set[ChangeCode] = set()
        for field_name, code in _LOT_FIELD_CODES:
            was, now = previous.get(field_name), current.get(field_name)
            if field_name == "cpvs":
                was, now = sorted(was or []), sorted(now or [])
            if _same_field(field_name, was, now) or code in seen_codes:
                # Un sol event per codi i lot: que pugin l'import i la moneda
                # alhora es un sol fet, no dos avisos.
                continue
            seen_codes.add(code)
            changes.append(Change(code, was, now, subject=key))
    return changes


#: Camps d'un document que son metadada i no contingut.
_DOCUMENT_METADATA = ("name", "document_type", "published_at")

#: Marca d'un document que ja no es a la publicacio completa vigent (radar
#: 3B-1). Com `source_removed`, nomes hi es quan val `True`: els documents
#: presents donen la mateixa fotografia que abans.
DOCUMENT_PRESENCE_FIELDS: frozenset[str] = frozenset({"removed"})

#: Tot el que un document aporta a la fotografia. `url` i `sha256` son la seva
#: identitat i els compara `_document_changes`; `removed` decideix si hi es; la
#: resta, metadada.
DOCUMENT_FIELDS: frozenset[str] = (
    frozenset({"url", "sha256"} | set(_DOCUMENT_METADATA)) | DOCUMENT_PRESENCE_FIELDS
)


def _document_changes(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """Els quatre casos d'identitat d'un document.

    La URL sola no identifica res. El mateix enllaç pot servir un fitxer nou
    —una correccio del plec— i un enllaç nou pot servir exactament el mateix
    fitxer, perque l'organ ha reorganitzat el seu web. Tractar les dues coses
    igual portaria o be a no adonar-se d'una modificacio del plec, o be a
    reanalitzar-ho tot i reobrir decisions humanes per un canvi d'adreça.

    Per aixo mana el SHA-256 quan el coneixem. Als documents que encara no s'han
    baixat, el SHA es `None` i nomes es pot comparar la URL: es una limitacio
    real i es preferible a inventar-se una identitat.

    Un document marcat `removed` (absent d'una publicacio completa posterior)
    es tracta com si no hi fos: marcar-lo dona `DOCUMENT_REMOVED` i desmarcar-lo
    `DOCUMENT_ADDED`, pels mateixos camins que una URL que desapareix o apareix.
    """
    present_before = [entry for entry in before.get("documents", []) if not entry.get("removed")]
    present_after = [entry for entry in after.get("documents", []) if not entry.get("removed")]
    old = {entry["url"]: entry for entry in present_before}
    new = {entry["url"]: entry for entry in present_after}
    changes: list[Change] = []

    old_by_sha = {entry["sha256"]: entry for entry in present_before if entry.get("sha256")}
    new_by_sha = {entry["sha256"]: entry for entry in present_after if entry.get("sha256")}

    for url in sorted(set(new) - set(old)):
        entry = new[url]
        sha = entry.get("sha256")
        previous = old_by_sha.get(sha) if sha else None
        if previous is not None and previous["url"] not in new:
            # URL nova amb contingut ja conegut: s'ha mogut, no ha canviat.
            changes.append(
                Change(ChangeCode.DOCUMENT_URL_CHANGED, previous["url"], url, subject=url)
            )
            continue
        changes.append(Change(ChangeCode.DOCUMENT_ADDED, None, entry.get("name"), subject=url))

    for url in sorted(set(old) - set(new)):
        entry = old[url]
        sha = entry.get("sha256")
        if sha and sha in new_by_sha:
            # Ja s'ha reportat com a canvi d'adreça al bucle anterior.
            continue
        changes.append(Change(ChangeCode.DOCUMENT_REMOVED, entry.get("name"), None, subject=url))

    for url in sorted(set(old) & set(new)):
        previous, current = old[url], new[url]
        old_sha, new_sha = previous.get("sha256"), current.get("sha256")
        if old_sha and new_sha and old_sha != new_sha:
            # Sabiem que hi havia dins i ara hi ha una altra cosa. Aixo si que
            # es una substitucio: la va fer l'organ, no nosaltres.
            changes.append(
                Change(ChangeCode.DOCUMENT_REPLACED, old_sha, new_sha, subject=url)
            )
            continue
        if not old_sha and new_sha:
            # Abans no l'haviem baixat i ara si. El document no ha canviat:
            # sempre havia sigut aquest, i l'unic que ha canviat es el que
            # nosaltres en sabem. Tractar-ho com una substitucio invalidaria els
            # requisits i reobriria decisions humanes per un motiu fals.
            changes.append(
                Change(ChangeCode.DOCUMENT_CONTENT_IDENTIFIED, None, new_sha, subject=url)
            )
            continue
        if any(
            not _same_field(name, previous.get(name), current.get(name))
            for name in _DOCUMENT_METADATA
        ):
            changes.append(
                Change(
                    ChangeCode.DOCUMENT_METADATA_CHANGED,
                    {name: previous.get(name) for name in _DOCUMENT_METADATA},
                    {name: current.get(name) for name in _DOCUMENT_METADATA},
                    subject=url,
                )
            )
    return changes


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """Els canvis estructurats entre dues fotografies normalitzades."""
    changes: list[Change] = []
    for field_name, code in _SCALAR_FIELDS:
        old, new = before.get(field_name), after.get(field_name)
        if not _same_field(field_name, old, new):
            # El `subject` es el nom del camp, no per decorar: dos camps poden
            # compartir codi i cal poder dir quin s'ha mogut.
            changes.append(Change(code, old, new, subject=field_name))
    changes += _source_presence_changes(before, after)
    changes += _status_changes(before, after)
    changes += _deadline_changes(before, after)
    changes += _cpv_changes(before, after)
    changes += _lot_changes(before, after)
    changes += _document_changes(before, after)
    return changes


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    """El resum que es desa a la revisio, per no re-diferenciar-la mai mes."""

    changes: tuple[Change, ...] = ()
    detection_version: str = CHANGE_DETECTION_VERSION
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def impacts(self) -> frozenset[ChangeImpact]:
        return impacts_of(list(self.changes))

    @property
    def is_material(self) -> bool:
        return has_material_changes(list(self.changes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "detection_version": self.detection_version,
            "impacts": sorted(impact.value for impact in self.impacts),
            "material": self.is_material,
            "counts": self.counts,
            "changes": [change.as_dict() for change in self.changes],
        }


def summarise(changes: list[Change]) -> ChangeSummary:
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.code.value] = counts.get(change.code.value, 0) + 1
    return ChangeSummary(changes=tuple(changes), counts=counts)
