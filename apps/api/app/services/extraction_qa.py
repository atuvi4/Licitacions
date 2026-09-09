"""Que tan de fiar es el text que hem extret d'un plec.

Aquesta capa NO llegeix el document: no diu que exigeix, ni si un requisit es
obligatori, ni res de dret. Respon una sola pregunta, i nomes amb el text:

    El que tenim sembla complet, o hi ha senyals que ens estem perdent alguna
    cosa que al PDF es veu i al text no hi ha?

Existeix per un cas concret i real. Al plec de Madrid (300/2025/03877) la pagina
70 acaba amb "para ello se aplicara la siguiente formula:" i la 71 comença amb
les definicions de les variables. L'expressio, entremig, no hi es: al PDF es una
imatge. Sense aquesta deteccio, el benchmark hauria comptat aixo com un error
del model, quan el model no podia extreure una cosa que el text no conte.

Distingir "el model ho ha fet malament" de "la font ja venia incompleta" es tot
el sentit del modul.

Dues coses que NO fa, i el motiu:

* **No fa OCR.** Nomes detecta quan probablement en caldra. L'OCR te un cost i
  una complexitat que mereixen una decisio propia, no una que s'hi coli.
* **No inventa la formula que falta.** Marcar la pagina es tot el que se'n pot
  dir honestament.

Sobre les imatges: pypdf sap comptar els objectes d'imatge d'una pagina, pero al
corpus real **totes** les pagines en tenen una o dues, perque els plecs porten
el segell de signatura electronica a cada full. Un recompte diferent de zero no
distingeix res, i per aixo aqui no hi ha cap regla que digui "te imatges, doncs
malament". Nomes es fa servir combinat amb text gairebe inexistent, que si que
es un indici: una pagina sense text pero amb objectes grafics es candidata a
OCR, i una pagina sense text ni objectes probablement es una separadora.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import ExtractionQuality, StrEnum

#: Versio de les heuristiques. Es desa a cada extraccio: dues qualitats
#: calculades amb criteris diferents no son comparables, i una revisio antiga ha
#: de poder dir amb quines regles es va avaluar.
QA_VERSION = "extraction-qa-v1"

#: Sota aquest nombre de caracters, una pagina amb text es practicament buida.
#: No es zero perque una pagina escanejada sovint deixa capçaleres o numeros.
NEARLY_EMPTY_CHARS = 60

#: Densitat per sota de la qual una pagina destaca respecte del document. Es
#: relativa a proposit: un plec de taules te menys text per pagina que un de
#: prosa, i un llindar absolut marcaria tot un tipus de document.
DENSITY_DROP_RATIO = 0.25

#: Nombre minim de pagines per poder parlar de densitat "normal" del document.
MIN_PAGES_FOR_DENSITY = 4

#: Proporcio de caracters de reemplaçament o de control que delata una
#: codificacio mal interpretada.
UNREADABLE_RATIO = 0.02


class IssueCode(StrEnum):
    """Que s'ha detectat. Codis tancats: la interficie en deriva el text."""

    EMPTY_PAGE = "EMPTY_PAGE"
    LOW_TEXT_DENSITY = "LOW_TEXT_DENSITY"
    #: Molt menys text que les pagines veines del mateix document.
    DENSITY_DROP = "DENSITY_DROP"
    #: El text anuncia una formula i despres no n'apareix cap.
    POSSIBLE_MISSING_FORMULA = "POSSIBLE_MISSING_FORMULA"
    #: El text anuncia una taula o un quadre i despres no s'hi veu.
    POSSIBLE_MISSING_TABLE = "POSSIBLE_MISSING_TABLE"
    #: La pagina acaba a mitja frase i la seguent no la continua.
    POSSIBLE_TRUNCATION = "POSSIBLE_TRUNCATION"
    #: Caracters de reemplaçament: la codificacio no s'ha llegit be.
    UNREADABLE_CHARACTERS = "UNREADABLE_CHARACTERS"
    #: Sense text pero amb objectes grafics: candidata a OCR.
    IMAGE_ONLY_PAGE = "IMAGE_ONLY_PAGE"
    #: El parser ha fallat en aquesta pagina o en el document.
    PARSER_WARNING = "PARSER_WARNING"


class QaStatus(StrEnum):
    GOOD = "good"
    WARNING = "warning"
    POOR = "poor"
    #: No s'ha avaluat. **No es `good`**: no autoritza cap conclusio sobre si el
    #: text era complet. Nomes apareix a l'agregat d'un run, mai al resultat
    #: d'analitzar unes pagines: si s'analitzen, ja se'n sap alguna cosa.
    UNKNOWN = "unknown"


#: Precedencia en combinar la qualitat de diverses fonts. `unknown` mana sobre
#: tot: si d'alguna de les fonts no en sabem res, la xifra agregada no es pot
#: presentar com una avaluacio del conjunt. El detall del que si que s'ha trobat
#: no es perd —viatja a la llista d'issues del run— pero l'etiqueta no pot dir
#: mes del que s'ha comprovat.
_STATUS_PRECEDENCE = (QaStatus.UNKNOWN, QaStatus.POOR, QaStatus.WARNING, QaStatus.GOOD)


def combine_status(statuses: list[str | None]) -> QaStatus:
    """La qualitat del conjunt de fonts que ha llegit un run.

    * cap font -> `unknown`: no hi ha res sobre que concloure.
    * alguna font sense QA (`None`) -> `unknown`.
    * si no, mana la pitjor de les avaluades.
    """
    if not statuses:
        return QaStatus.UNKNOWN
    seen = set()
    for status in statuses:
        if status is None:
            return QaStatus.UNKNOWN
        try:
            seen.add(QaStatus(status))
        except ValueError:
            # Un valor que no reconeixem tampoc autoritza cap conclusio.
            return QaStatus.UNKNOWN
    for candidate in _STATUS_PRECEDENCE:
        if candidate in seen:
            return candidate
    return QaStatus.UNKNOWN


#: Quins codis, per si sols, fan que el document no es pugui considerar bo.
#: `POOR` es reserva per al que compromet el document sencer; el que afecta una
#: pagina concreta es un avis, perque la resta del text segueix sent utilitzable.
POOR_CODES = frozenset({IssueCode.UNREADABLE_CHARACTERS, IssueCode.PARSER_WARNING})

#: Proporcio de pagines amb problemes a partir de la qual el document es pobre.
POOR_PAGE_RATIO = 0.3


@dataclass(frozen=True, slots=True)
class PageInput:
    """El que se sap d'una pagina, sense tornar a obrir el PDF."""

    page_number: int
    text: str
    #: Objectes d'imatge detectats. `None` = no s'ha pogut mirar.
    image_count: int | None = None
    failed: bool = False


@dataclass(frozen=True, slots=True)
class Issue:
    code: IssueCode
    #: `None` quan afecta el document sencer.
    page_number: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentQa:
    status: QaStatus
    issues: tuple[Issue, ...]
    qa_version: str = QA_VERSION

    def codes(self) -> set[IssueCode]:
        return {issue.code for issue in self.issues}

    def pages_with(self, code: IssueCode) -> list[int]:
        return sorted(
            issue.page_number
            for issue in self.issues
            if issue.code == code and issue.page_number is not None
        )


# --------------------------------------------------------------------------- #
# Referencies a contingut que hauria de venir a continuacio                     #
# --------------------------------------------------------------------------- #

#: Frases que anuncien una formula. En castella i catala, que es el que hi ha al
#: corpus. No s'hi posen expressions genèriques com "calcular", que apareixen a
#: qualsevol plec i inundarien el resultat d'avisos inutils.
FORMULA_ANNOUNCEMENTS = re.compile(
    r"(?:"
    r"siguiente\s+f[oó]rmula"
    r"|f[oó]rmula\s+(?:a\s+aplicar|siguiente)"
    r"|se\s+aplicar[aá]\s+la\s+f[oó]rmula"
    r"|mediante\s+la\s+f[oó]rmula"
    r"|de\s+acuerdo\s+con\s+la\s+expresi[oó]n"
    r"|seg[uú]n\s+la\s+siguiente\s+expresi[oó]n"
    r"|seg[üu]ent\s+f[oó]rmula"
    r"|f[oó]rmula\s+seg[üu]ent"
    r")",
    re.IGNORECASE,
)

#: Frases que anuncien una taula o un quadre.
TABLE_ANNOUNCEMENTS = re.compile(
    r"(?:"
    r"siguiente\s+(?:tabla|cuadro)"
    r"|(?:tabla|cuadro)\s+siguiente"
    r"|seg[uú]n\s+el\s+cuadro\s+siguiente"
    r"|se\s+indican?\s+a\s+continuaci[oó]n"
    r"|se\s+detallan?\s+a\s+continuaci[oó]n"
    r"|seg[üu]ent\s+(?:taula|quadre)"
    r")",
    re.IGNORECASE,
)

#: El peu de signatura electronica, que als plecs de PLACSP surt a CADA pagina.
#: S'ha de treure abans de qualsevol heuristica: porta una URL amb un
#: `codigoVerificacion=XXXX` que s'assembla molt a una assignacio, i sense
#: treure'l la pagina de Madrid semblava que si que contenia la formula.
SIGNATURE_NOISE = re.compile(
    r"(?:https?://\S+|www[\w.-]*\.\S+)"
    r"|^.*(?:"
    r"c[oó]d(?:igo|\.)\s+(?:de\s+)?(?:verificaci[oó]n|validaci[oó]n)"
    r"|firmado\s+(?:por|electr[oó]nicamente)"
    r"|signat\s+electr[oò]nicament"
    r"|documento\s+firmado"
    r"|verificaci[oó]n\s*:"
    # La capçalera amb el numero d'expedient tambe es a cada pagina.
    r"|^\s*expedient[e]?\s*:"
    r"|^\s*p[aá]gina\s*:?\s*\d+\s+d[e']\s*\d+"
    r").*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_signature_noise(text: str) -> str:
    """Text net de repeticions de pagina i amb els simbols matematics unificats.

    Dues coses, i totes dues venen de casos reals del corpus:

    * **Fora URLs i peus de signatura.** Son a cada pagina i l'adreça de
      verificacio porta un signe igual seguit d'un codi llarg, que el detector
      llegia com una formula.
    * **NFKC.** Un plec escrit amb font matematica produeix lletres Unicode
      (`𝑃 = 𝑀𝑂/𝑂𝐿 x 49`) que no son `[A-Z]`. Sense normalitzar, la formula hi
      es i el detector deia que faltava.
    """
    return SIGNATURE_NOISE.sub(" ", unicodedata.normalize("NFKC", text or ""))


#: Una expressio matematica plausible: dos operands separats per un OPERADOR
#: aritmetic. L'operador es el que la distingeix d'una definicio de variable
#: ("P: es la puntuacion de la oferta"), que no es cap formula.
#:
#: El cas que obliga a filar prim: `300/2025` —un numero d'expedient, i tambe
#: qualsevol data— son dos numeros amb una barra pel mig, i llegir-ho com una
#: divisio feia creure que la formula hi era. Per aixo una barra nomes compta
#: com a operador si algun dels dos costats es simbolic.
#: Un operand simbolic: un parentesi, una sigla en majuscules (`OM`, `PBL`) o una
#: paraula amb majuscula inicial (`Oferta`). Deixa fora els connectors en
#: minuscula, i aixo importa: "y/o" apareix a qualsevol plec i es llegia com una
#: divisio, cosa que feia creure que la formula que faltava hi era.
_NUM = r"\d[\d.,]*"
_SYM = r"(?:\([^)\n]{1,60}\)|\b[A-Z]{1,6}\b|\b[A-ZÀ-Ú][a-zà-ú]{1,11}\b)"
EXPRESSION = re.compile(
    # `75 x (OM/OF)`, `10 x Oferta`: una multiplicacio explicita.
    rf"{_NUM}\s*[x×*]\s*(?:{_NUM}|{_SYM})"
    # `OM/OF`, `Oferta mas baja / Oferta a valorar`: operand simbolic.
    rf"|{_SYM}\s*[x×*/]\s*(?:{_NUM}|{_SYM})"
    # Una assignacio seguida d'una operacio: `PL=10 x Oferta`, `P=(pm*O)/mo`,
    # `P = pm x ( 1 + log(mo/O))`. Es la forma general, i evita anar afegint un
    # patro nou cada cop que un plec escriu la formula d'una altra manera. El
    # signe igual es el que la separa de la prosa: dins d'una assignacio, una
    # barra o un aspa son un operador i no una conjuncio.
    r"|\b[A-Za-z]{1,10}\s*=\s*[^\n]{0,40}?[\w)]\s*[x×*/+]\s*[\w(]"
)

#: Separadors explicits de taula.
_TABLE_SEPARATORS = re.compile(r"\|.*\||\t.*\t")
#: Una linia amb dos o mes valors numerics: la forma tipica d'una fila.
_NUMERIC_ROW = re.compile(r"\d[\d.,]*(?:\s*%|\s*(?:punts|puntos|euros|€|h))?\s+\S*\s*\d[\d.,]*")


def _looks_like_table(text: str) -> bool:
    """Si el text conte alguna cosa que sembla una taula textualitzada.

    No es una deteccio fina i no cal que ho sigui: nomes serveix per **no**
    avisar. Un fals positiu aqui fa callar un avis; un fals negatiu n'inventa un,
    i un detector que avisa a cada quadre de puntuacions no el mirara ningu.
    """
    if _TABLE_SEPARATORS.search(text):
        return True
    # Dues files amb numeros ja son una taula: una de sola pot ser una frase.
    return sum(1 for line in text.splitlines() if _NUMERIC_ROW.search(line)) >= 2

#: Quantes pagines seguents es miren per trobar el que s'anunciava. Dues: una
#: formula pot caure al salt de pagina, pero si apareix tres pagines mes enlla ja
#: no es "a continuacio".
LOOKAHEAD_PAGES = 2


def _after_announcement(text: str, pattern: re.Pattern[str]) -> str | None:
    """El text que ve DESPRES de l'ultim anunci, dins la mateixa pagina."""
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    return text[matches[-1].end() :]


def _looks_like_expression(text: str) -> bool:
    return EXPRESSION.search(text) is not None


def find_missing_content(pages: list[PageInput]) -> list[Issue]:
    """Anuncis de formula o de taula que no tenen continuacio visible.

    Es mira primer la resta de la mateixa pagina i despres l'inici de les
    seguents: al corpus real, la formula de `422-2026` esta a la mateixa pagina
    que l'anunci i la de Madrid hauria d'estar al salt de pagina.
    """
    issues: list[Issue] = []
    # Totes les comparacions es fan sobre el text net: el peu de signatura
    # apareix a cada pagina i falsejaria tant la deteccio com l'absencia.
    clean = {page.page_number: strip_signature_noise(page.text) for page in pages}
    by_number = {page.page_number: page for page in pages}

    for page in pages:
        page_text = clean[page.page_number]
        for pattern, code, looks_ok in (
            (FORMULA_ANNOUNCEMENTS, IssueCode.POSSIBLE_MISSING_FORMULA, _looks_like_expression),
            (TABLE_ANNOUNCEMENTS, IssueCode.POSSIBLE_MISSING_TABLE, _looks_like_table),
        ):
            tail = _after_announcement(page_text, pattern)
            if tail is None:
                continue
            if looks_ok(tail):
                continue
            following = "".join(
                clean[page.page_number + offset]
                for offset in range(1, LOOKAHEAD_PAGES + 1)
                if page.page_number + offset in by_number
            )
            if looks_ok(following):
                continue
            issues.append(
                Issue(
                    code=code,
                    page_number=page.page_number,
                    detail={
                        "announcement": pattern.search(page_text).group(0),
                        "searched_pages": [
                            page.page_number + offset
                            for offset in range(0, LOOKAHEAD_PAGES + 1)
                            if page.page_number + offset in by_number
                        ],
                    },
                )
            )
    return issues


# --------------------------------------------------------------------------- #
# Senyals de pagina                                                             #
# --------------------------------------------------------------------------- #

_REPLACEMENT = re.compile(r"[�￾]")


def _unreadable_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = len(_REPLACEMENT.findall(text))
    bad += sum(1 for char in text if unicodedata.category(char) == "Co")
    return bad / len(text)


def page_issues(pages: list[PageInput]) -> list[Issue]:
    """Els problemes de cada pagina, mirats un a un i contra el document."""
    issues: list[Issue] = []
    lengths = [len(page.text) for page in pages]
    non_empty = [length for length in lengths if length > NEARLY_EMPTY_CHARS]
    # Mediana i no mitjana: una sola pagina de portada no ha de moure el llistó.
    median = 0.0
    if len(non_empty) >= MIN_PAGES_FOR_DENSITY:
        ordered = sorted(non_empty)
        median = float(ordered[len(ordered) // 2])

    for page in pages:
        length = len(page.text)
        if page.failed:
            issues.append(
                Issue(IssueCode.PARSER_WARNING, page.page_number, {"reason": "page_failed"})
            )
            continue
        if length == 0 or length <= NEARLY_EMPTY_CHARS:
            code = (
                IssueCode.IMAGE_ONLY_PAGE
                if page.image_count
                else IssueCode.EMPTY_PAGE
            )
            issues.append(
                Issue(code, page.page_number, {"chars": length, "images": page.image_count})
            )
            continue

        ratio = _unreadable_ratio(page.text)
        if ratio > UNREADABLE_RATIO:
            issues.append(
                Issue(
                    IssueCode.UNREADABLE_CHARACTERS,
                    page.page_number,
                    {"ratio": round(ratio, 4)},
                )
            )
        if median and length < median * DENSITY_DROP_RATIO:
            issues.append(
                Issue(
                    IssueCode.DENSITY_DROP,
                    page.page_number,
                    {"chars": length, "document_median": median},
                )
            )
    return issues


def summarise(pages: list[PageInput], issues: list[Issue]) -> QaStatus:
    """El veredicte del document sencer.

    Un avis en una pagina no fa dolent un plec de cent: la resta del text
    segueix sent utilitzable, i marcar-lo tot com a pobre faria que ningu es
    mires els avisos que si que importen.
    """
    if not pages:
        return QaStatus.POOR
    codes = {issue.code for issue in issues}
    if codes & POOR_CODES:
        return QaStatus.POOR

    affected = {issue.page_number for issue in issues if issue.page_number is not None}
    if len(affected) / len(pages) > POOR_PAGE_RATIO:
        return QaStatus.POOR
    return QaStatus.WARNING if issues else QaStatus.GOOD


def analyse(
    pages: list[PageInput], *, extraction_quality: ExtractionQuality | None = None
) -> DocumentQa:
    """La QA sencera d'una extraccio. Determinista i sense tocar el PDF."""
    issues = page_issues(pages) + find_missing_content(pages)
    if extraction_quality == ExtractionQuality.LOW_TEXT:
        issues.append(Issue(IssueCode.LOW_TEXT_DENSITY, None, {"source": "extraction"}))
    issues.sort(key=lambda issue: (issue.page_number or 0, issue.code.value))
    return DocumentQa(status=summarise(pages, issues), issues=tuple(issues))
