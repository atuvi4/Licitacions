"""Quin paper fa cada document d'una licitacio, i que no quadra del conjunt.

**No depen de per on han arribat els documents.** Rep fets sobre documents
(nom, tipus, bytes, text per pagina) i els classifica. Tant li fa si venen d'un
ZIP, d'una carpeta de Drive, d'una plataforma de contractacio o d'una pujada
individual: la recepcio (`app/reception`) es una capa a part i aquest modul no
la coneix.

**Es classifica el conjunt, no l'entrega.** Les incoherencies que importen
(una copia, un document citat que falta) son de l'expedient sencer. Si el DOC-04
arriba dema per un altre canal, la classificacio seguent ja no el troba a
faltar.

**Classificar no es endevinar.** La pujada d'un sol fitxer no dedueix el tipus
del nom (vegeu `tender_document_upload`), i continua sent aixi. Aquest modul
tampoc: **cada rol porta els senyals que el sostenen** (el nom, el titol de la
primera pagina, el codi intern) i la confianca que se'n deriva. Una persona el
pot corregir, i la correccio es guarda a part del que va dir el classificador.

**Mana el contingut, no el nom.** El nom del fitxer l'ha posat algu, potser
malament; la primera pagina l'ha escrita l'organ de contractacio. Quan
discrepen, el rol es el del contingut i la discrepancia es una troballa. Es el
cas real de l'Escala: `04_Annex_Mestre_Inventaris…pdf` porta el text sencer del
DOC-03.

**El titol es el primer que apareix.** La primera pagina d'un PCAP sovint cita
el PPT ("d'acord amb el Plec de Prescripcions Tecniques…"), i la d'un quadre de
caracteristiques cita el PCAP. Per aixo no val "conte X": val quin patro surt
**abans** dins la zona del titol.

**Nomes el que es segur arriba a l'analisi.** El `TenderDocumentType` d'un
document nomes surt del rol quan la confianca es alta i el document no es un
duplicat. Si no, `OTHER`, que vol dir que l'analisi automatica no el tria fins
que una persona ho confirma: un duplicat analitzat fabricaria requisits
duplicats, i un rol dubtos, requisits d'on no toca.

Modul pur: rep fets i retorna decisions i troballes. No toca cap sessio.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.models.enums import (
    ClassificationFindingCode,
    DocumentRole,
    FindingSeverity,
    RoleConfidence,
    TenderDocumentType,
)

CLASSIFIER_VERSION = "document-roles-v1"

#: Caracters de la primera pagina on es busca el titol.
TITLE_ZONE_CHARS = 1500
#: Un titol trobat abans d'aqui es alta confianca: es la capcalera.
HIGH_CONFIDENCE_CHARS = 600
#: Per sota d'aquest text normalitzat, dos documents iguals no diuen res.
MIN_TEXT_FOR_DUPLICATE = 200
_MAX_REFERENCES = 5

ROLE_TO_DOCUMENT_TYPE: dict[DocumentRole, TenderDocumentType] = {
    DocumentRole.PCAP: TenderDocumentType.ADMINISTRATIVE_TERMS,
    # El quadre de caracteristiques es part del PCAP: hi ha la solvencia, els
    # criteris i les garanties. S'analitza com a tal.
    DocumentRole.CHARACTERISTICS_TABLE: TenderDocumentType.ADMINISTRATIVE_TERMS,
    DocumentRole.PPT: TenderDocumentType.TECHNICAL_TERMS,
    DocumentRole.OFFER_MODEL: TenderDocumentType.ADDITIONAL,
    DocumentRole.INVENTORY_ANNEX: TenderDocumentType.ADDITIONAL,
    DocumentRole.NOTICE: TenderDocumentType.NOTICE,
    DocumentRole.JUSTIFICATION_REPORT: TenderDocumentType.OTHER,
    DocumentRole.APPROVAL_RESOLUTION: TenderDocumentType.OTHER,
    DocumentRole.ADMINISTRATIVE_FORM: TenderDocumentType.OTHER,
    DocumentRole.OTHER: TenderDocumentType.OTHER,
}

#: Rols que el conjunt de documents d'una licitacio ha de tenir.
REQUIRED_ROLES: tuple[DocumentRole, ...] = (DocumentRole.PCAP, DocumentRole.PPT)

#: Parelles que no son una discrepancia: molts ajuntaments publiquen com a
#: "PCAP" un fitxer que comenca pel quadre de caracteristiques.
_COMPATIBLE = frozenset({frozenset({DocumentRole.PCAP, DocumentRole.CHARACTERISTICS_TABLE})})

#: Formats dels quals la capa documental extreu text per a l'analisi (els parsers de
#: `document_parsers`: PDF, text pla, DOCX, XLSX i XML).
ANALYZABLE_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/xml",
        "text/xml",
    }
)
_SPREADSHEETS = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }
)


def normalize(text: str) -> str:
    """Minuscules, sense accents, apostrofs rectes i espais col·lapsats."""
    text = text.replace("’", "'").replace("‘", "'").replace("·", ".")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _patterns(*items: tuple[DocumentRole, str]) -> tuple[tuple[DocumentRole, re.Pattern], ...]:
    return tuple((role, re.compile(pattern)) for role, pattern in items)


#: Titols de primera pagina, en catala i castella. Sobre text normalitzat.
CONTENT_PATTERNS = _patterns(
    (
        DocumentRole.CHARACTERISTICS_TABLE,
        r"quadre (de )?caracteristiques|cuadro (de )?caracteristicas|quadre resum|cuadro resumen",
    ),
    (
        DocumentRole.PCAP,
        r"plec de clausules administratives|pliego de clausulas administrativas"
        r"|plec de condicions administratives|pliego de condiciones administrativas",
    ),
    (
        DocumentRole.PPT,
        r"plec de prescripcions tecniques|pliego de prescripciones tecnicas"
        r"|plec de condicions tecniques|pliego de condiciones tecnicas",
    ),
    (
        DocumentRole.OFFER_MODEL,
        r"model d'oferta|model de proposicio|modelo de oferta|modelo de proposicion"
        r"|proposicio economica|proposicion economica",
    ),
    (
        DocumentRole.INVENTORY_ANNEX,
        r"annex (mestre )?d'inventari|inventari (de |d')(ubicacions|seus|linies|extensions"
        r"|equipaments|serveis)|anexo de inventario|inventario de",
    ),
    (
        DocumentRole.JUSTIFICATION_REPORT,
        r"memoria justificativa|informe de necessitat|informe de necesidad|informe justificatiu",
    ),
    (
        DocumentRole.APPROVAL_RESOLUTION,
        r"(junta de govern|ple de l'ajuntament|ple municipal|junta de gobierno)"
        r".{0,200}(va adoptar|adopta|acorda|adopto|acuerda)"
        r"|(decret|resolucio|decreto|resolucion) (d'|de )?(alcaldia|presidencia)",
    ),
    (
        DocumentRole.ADMINISTRATIVE_FORM,
        r"document europeu unic de contractacio|documento europeo unico de contratacion"
        r"|declaracio responsable|declaracion responsable",
    ),
    (DocumentRole.NOTICE, r"anunci de licitacio|anuncio de licitacion"),
)

#: Noms de fitxer. Sobre el nom normalitzat, amb separadors convertits en espais.
FILENAME_PATTERNS = _patterns(
    (
        DocumentRole.CHARACTERISTICS_TABLE,
        r"quadre (de )?caracteristiques|cuadro (de )?caracteristicas",
    ),
    (
        DocumentRole.PCAP,
        r"\bpcap\b|\bpcag\b|clausules administratives|clausulas administrativas"
        r"|plec administratiu|pliego administrativo",
    ),
    (
        DocumentRole.PPT,
        r"\bppt\b|\bpptp\b|prescripcions tecniques|prescripciones tecnicas"
        r"|plec tecnic|pliego tecnico",
    ),
    (
        DocumentRole.OFFER_MODEL,
        r"\bmodel(o)? (d'|de |d )?(oferta|proposicio|proposicion)|oferta economica"
        r"|criteris automatics|criterios automaticos",
    ),
    (DocumentRole.INVENTORY_ANNEX, r"inventari|inventario"),
    (
        DocumentRole.JUSTIFICATION_REPORT,
        r"memoria justificativa|informe (de )?necessitat|informe (de )?necesidad",
    ),
    (
        DocumentRole.APPROVAL_RESOLUTION,
        r"\bacord\b|\bjgl\b|\bdecret\b|\bresolucio\b|\bdecreto\b|\bresolucion\b",
    ),
    (
        DocumentRole.ADMINISTRATIVE_FORM,
        r"\bdeuc\b|declaracio responsable|declaracion responsable",
    ),
    (DocumentRole.NOTICE, r"\banunci\b|\banuncio\b"),
)

_OFFER_WORDS = re.compile(r"\b(baixa|oferta|puntuacio|punts|preu|pressupost|baja|precio|puntos)\b")
_INVENTORY_WORDS = re.compile(
    r"\b(ubicacio|ubicacions|adreca|extensio|extensions|linia|linies|inventari|ubicacion|direccion)\b"
)
_DECLARED_CODE = re.compile(r"\bcodi(?:go)?\s*:?\s*doc-(\d{1,3})\b")
_REFERENCED_CODE = re.compile(r"\bDOC-(\d{1,3})\b", re.IGNORECASE)
_FILENAME_SEPARATORS = re.compile(r"[_\-.()\[\]]+")


@dataclass(frozen=True, slots=True)
class DocumentFacts:
    """El que se sap d'un document de la licitacio, vingui del canal que vingui."""

    key: str
    position: int
    path: str
    filename: str
    mime_type: str
    sha256: str
    #: Text per pagina. `None` si no n'hi ha cap (format no llegible o escanejat).
    pages: tuple[str, ...] | None = None
    #: D'on surt el text: `extraction` (el de l'analisi) o `ooxml` (nomes per
    #: classificar).
    text_source: str | None = None


@dataclass(frozen=True, slots=True)
class RoleDecision:
    key: str
    role: DocumentRole
    confidence: RoleConfidence
    filename_role: DocumentRole | None
    content_role: DocumentRole | None
    content_position: int | None
    title_line: str | None
    declared_code: str | None
    text_available: bool
    page_count: int
    #: El document del qual aquest es una copia (bytes o text), si n'es una.
    duplicate_of: str | None
    #: Com el tractara l'analisi mentre ningu no ho revisi.
    document_type: TenderDocumentType

    def signals(self) -> dict:
        return {
            "filename_role": self.filename_role,
            "content_role": self.content_role,
            "content_position": self.content_position,
            "title_line": self.title_line,
            "declared_code": self.declared_code,
            "text_available": self.text_available,
            "page_count": self.page_count,
            "duplicate_of": self.duplicate_of,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    code: ClassificationFindingCode
    severity: FindingSeverity
    key: str | None = None
    related_key: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass(slots=True)
class Classification:
    decisions: dict[str, RoleDecision]
    findings: list[Finding]
    version: str = CLASSIFIER_VERSION


# --------------------------------------------------------------------------- #
# Senyals d'un sol document                                                    #
# --------------------------------------------------------------------------- #


def _earliest(patterns, text: str) -> tuple[DocumentRole, int] | None:
    best: tuple[DocumentRole, int] | None = None
    for role, pattern in patterns:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[1]):
            best = (role, match.start())
    return best


def filename_role(filename: str) -> DocumentRole | None:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    found = _earliest(FILENAME_PATTERNS, normalize(_FILENAME_SEPARATORS.sub(" ", stem)))
    return found[0] if found else None


def _title_line(first_page: str, role: DocumentRole) -> str | None:
    pattern = dict(CONTENT_PATTERNS)[role]
    for line in first_page.splitlines():
        if pattern.search(normalize(line)):
            return re.sub(r"\s+", " ", line).strip()[:200]
    return None


def content_role(
    first_page: str, *, spreadsheet: bool = False
) -> tuple[DocumentRole, int | None, RoleConfidence, str | None] | None:
    zone = normalize(first_page)[:TITLE_ZONE_CHARS]
    found = _earliest(CONTENT_PATTERNS, zone)
    if found:
        role, position = found
        confidence = (
            RoleConfidence.HIGH if position < HIGH_CONFIDENCE_CHARS else RoleConfidence.MEDIUM
        )
        return role, position, confidence, _title_line(first_page, role)
    if spreadsheet:
        # Un full de calcul no te titol de plec. El que el delata son els rotuls.
        text = normalize(first_page)
        offer = {m.group(1) for m in _OFFER_WORDS.finditer(text)}
        inventory = {m.group(1) for m in _INVENTORY_WORDS.finditer(text)}
        if len(offer) >= 3:
            return DocumentRole.OFFER_MODEL, None, RoleConfidence.MEDIUM, None
        if len(inventory) >= 2:
            return DocumentRole.INVENTORY_ANNEX, None, RoleConfidence.MEDIUM, None
    return None


def declared_code(first_page: str) -> str | None:
    match = _DECLARED_CODE.search(normalize(first_page)[:TITLE_ZONE_CHARS])
    return _canonical_code(match.group(1)) if match else None


def _canonical_code(digits: str) -> str:
    return f"DOC-{int(digits):02d}"


def _text_hash(pages: tuple[str, ...] | None) -> str | None:
    if not pages:
        return None
    text = normalize(" ".join(pages))
    if len(text) < MIN_TEXT_FOR_DUPLICATE:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_text(pages: tuple[str, ...] | None) -> bool:
    return bool(pages) and any(page.strip() for page in pages)


# --------------------------------------------------------------------------- #
# El conjunt de documents                                                     #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Draft:
    facts: DocumentFacts
    role: DocumentRole
    confidence: RoleConfidence
    filename_role: DocumentRole | None
    content_role: DocumentRole | None
    content_position: int | None
    title_line: str | None
    declared_code: str | None
    text_available: bool
    duplicate_of: str | None = None

    @property
    def consistent(self) -> bool:
        return self.filename_role is not None and self.filename_role == self.content_role


def _decide(facts: DocumentFacts) -> _Draft:
    text_available = _has_text(facts.pages)
    first_page = next((p for p in facts.pages or () if p.strip()), "") if text_available else ""
    by_name = filename_role(facts.filename)
    by_content = (
        content_role(first_page, spreadsheet=facts.mime_type in _SPREADSHEETS)
        if text_available
        else None
    )

    if by_content is not None:
        role, position, confidence, title = by_content
        # Dos senyals independents que coincideixen valen mes que un.
        if by_name == role and confidence is RoleConfidence.MEDIUM:
            confidence = RoleConfidence.HIGH
    elif by_name is not None:
        role, position, title = by_name, None, None
        # Nomes el nom: mai es alta. Si s'ha pogut llegir el text i no el
        # contradiu, mitjana; si no s'ha pogut llegir, baixa.
        confidence = RoleConfidence.MEDIUM if text_available else RoleConfidence.LOW
    else:
        role, position, title, confidence = DocumentRole.OTHER, None, None, RoleConfidence.LOW

    return _Draft(
        facts=facts,
        role=role,
        confidence=confidence,
        filename_role=by_name,
        content_role=by_content[0] if by_content else None,
        content_position=position,
        title_line=title,
        declared_code=declared_code(first_page) if text_available else None,
        text_available=text_available,
    )


def _canonical(group: list[_Draft]) -> _Draft:
    """Dins d'un grup de copies, la que es fiable: la que diu el que es."""
    return sorted(group, key=lambda d: (not d.consistent, d.facts.position))[0]


def _excerpt(text: str, start: int, end: int, radius: int = 80) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - radius) : end + radius]).strip()


def classify_documents(documents: Sequence[DocumentFacts]) -> Classification:
    drafts = [_decide(facts) for facts in sorted(documents, key=lambda f: f.position)]
    by_key = {d.facts.key: d for d in drafts}
    findings: list[Finding] = []

    # 1. El nom contradiu el contingut.
    for d in drafts:
        if (
            d.filename_role is not None
            and d.content_role is not None
            and d.filename_role != d.content_role
            and frozenset({d.filename_role, d.content_role}) not in _COMPATIBLE
        ):
            findings.append(
                Finding(
                    code=ClassificationFindingCode.TITLE_CONTENT_MISMATCH,
                    severity=(
                        FindingSeverity.HIGH
                        if d.confidence is RoleConfidence.HIGH
                        else FindingSeverity.MEDIUM
                    ),
                    key=d.facts.key,
                    detail={
                        "filename_role": d.filename_role,
                        "content_role": d.content_role,
                        "title_line": d.title_line,
                    },
                )
            )

    # 2. Els mateixos bytes dues vegades.
    groups: dict[str, list[_Draft]] = {}
    for d in drafts:
        groups.setdefault(d.facts.sha256, []).append(d)
    representatives: list[_Draft] = []
    for group in groups.values():
        keep = _canonical(group)
        representatives.append(keep)
        for d in group:
            if d is keep:
                continue
            d.duplicate_of = keep.facts.key
            findings.append(
                Finding(
                    code=ClassificationFindingCode.DUPLICATE_BYTES,
                    severity=FindingSeverity.MEDIUM,
                    key=d.facts.key,
                    related_key=keep.facts.key,
                    detail={"sha256": d.facts.sha256},
                )
            )

    # 3. Fitxers diferents amb el mateix text: un dels dos no es el que diu.
    text_groups: dict[str, list[_Draft]] = {}
    for d in representatives:
        digest = _text_hash(d.facts.pages)
        if digest is not None:
            text_groups.setdefault(digest, []).append(d)
    for group in text_groups.values():
        if len(group) < 2:
            continue
        keep = _canonical(group)
        for d in sorted(group, key=lambda x: x.facts.position):
            if d is keep:
                continue
            d.duplicate_of = keep.facts.key
            findings.append(
                Finding(
                    code=ClassificationFindingCode.DUPLICATE_TEXT,
                    severity=FindingSeverity.HIGH,
                    key=d.facts.key,
                    related_key=keep.facts.key,
                    detail={
                        "declared_code": d.declared_code,
                        "filename_role": d.filename_role,
                        "content_role": d.content_role,
                    },
                )
            )

    # 4. Codis interns: repetits amb textos diferents, i citats pero absents.
    declared: dict[str, list[_Draft]] = {}
    for d in drafts:
        if d.declared_code and d.duplicate_of is None:
            declared.setdefault(d.declared_code, []).append(d)
    for code, owners in declared.items():
        for d in owners[1:]:
            findings.append(
                Finding(
                    code=ClassificationFindingCode.DUPLICATE_INTERNAL_CODE,
                    severity=FindingSeverity.MEDIUM,
                    key=d.facts.key,
                    related_key=owners[0].facts.key,
                    detail={"code": code},
                )
            )
    all_declared = {d.declared_code for d in drafts if d.declared_code}
    if all_declared:
        references: dict[str, list[dict]] = {}
        for d in drafts:
            for page_number, page in enumerate(d.facts.pages or (), start=1):
                for match in _REFERENCED_CODE.finditer(page):
                    code = _canonical_code(match.group(1))
                    if code in all_declared:
                        continue
                    refs = references.setdefault(code, [])
                    if len(refs) < _MAX_REFERENCES:
                        refs.append(
                            {
                                "document": d.facts.key,
                                "path": d.facts.path,
                                "page": page_number,
                                "excerpt": _excerpt(page, match.start(), match.end()),
                            }
                        )
        for code in sorted(references):
            findings.append(
                Finding(
                    code=ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING,
                    severity=FindingSeverity.HIGH,
                    detail={
                        "code": code,
                        "declared_codes": sorted(all_declared),
                        "references": references[code],
                    },
                )
            )

    # 5. Qualitat de lectura, format i rol. Tot aixo es PROCESSABILITAT: el document
    # hi es; nomes diu si l'analisi en podra llegir el text, mai que falti res.
    for d in drafts:
        mime = d.facts.mime_type
        # El text llegit nomes per classificar (`ooxml`) no arriba a l'analisi: un DOCX
        # que encara no s'ha extret no es pot analitzar, encara que se'n sapigui el rol.
        readable = d.text_available and d.facts.text_source != "ooxml"
        if mime in ANALYZABLE_MIME_TYPES and not readable:
            detail = {"mime_type": mime}
            if d.text_available:
                detail["read_for_classification"] = True
            findings.append(
                Finding(
                    code=ClassificationFindingCode.NO_TEXT,
                    severity=FindingSeverity.MEDIUM,
                    key=d.facts.key,
                    detail=detail,
                )
            )
        elif mime not in ANALYZABLE_MIME_TYPES:
            findings.append(
                Finding(
                    code=ClassificationFindingCode.FORMAT_NOT_ANALYZABLE,
                    severity=FindingSeverity.LOW,
                    key=d.facts.key,
                    detail={"mime_type": mime, "read_for_classification": d.text_available},
                )
            )
        if d.role is DocumentRole.OTHER and d.duplicate_of is None:
            findings.append(
                Finding(
                    code=ClassificationFindingCode.ROLE_UNDETERMINED,
                    severity=FindingSeverity.LOW,
                    key=d.facts.key,
                )
            )

    # 6. La licitacio ha de tenir PCAP i PPT.
    present = {d.role for d in drafts if d.duplicate_of is None}
    for role in REQUIRED_ROLES:
        if role not in present:
            findings.append(
                Finding(
                    code=ClassificationFindingCode.REQUIRED_ROLE_MISSING,
                    severity=FindingSeverity.HIGH,
                    detail={"role": role},
                )
            )

    decisions = {}
    for d in drafts:
        confident = d.confidence is RoleConfidence.HIGH and d.duplicate_of is None
        decisions[d.facts.key] = RoleDecision(
            key=d.facts.key,
            role=d.role,
            confidence=d.confidence,
            filename_role=d.filename_role,
            content_role=d.content_role,
            content_position=d.content_position,
            title_line=d.title_line,
            declared_code=d.declared_code,
            text_available=d.text_available,
            page_count=len(d.facts.pages or ()),
            duplicate_of=d.duplicate_of,
            document_type=(
                ROLE_TO_DOCUMENT_TYPE[d.role] if confident else TenderDocumentType.OTHER
            ),
        )

    order = {s: i for i, s in enumerate(FindingSeverity)}
    position = {key: d.facts.position for key, d in by_key.items()}
    findings.sort(key=lambda f: (order[f.severity], position.get(f.key, -1)))
    return Classification(decisions=decisions, findings=findings)
