"""Enumeracions del domini.

Es guarden com a text (VARCHAR + CHECK) per facilitar migracions futures.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - conveniencia
        return self.value


class MembershipRole(StrEnum):
    """Rols preparats. En aquesta iteracio nomes es comprova la pertinenca, no el rol."""

    OWNER = "owner"
    ADMIN = "admin"
    BID_MANAGER = "bid_manager"
    TECHNICAL = "technical"
    VIEWER = "viewer"


class CompanyPlatformStatus(StrEnum):
    """Estat d'una empresa client vist per l'Administracio Tuvi.

    De moment nomes es registra i s'audita: `paused` encara NO bloqueja l'acces
    dels usuaris del client (fase posterior).
    """

    PILOT = "pilot"
    ACTIVE = "active"
    PAUSED = "paused"


class SpecializationLevel(StrEnum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class TerritoryType(StrEnum):
    MUNICIPALITY = "municipality"
    COMARCA = "comarca"
    PROVINCE = "province"
    AUTONOMOUS_REGION = "autonomous_region"
    COUNTRY = "country"
    NUTS = "nuts"
    OTHER = "other"


class VerificationStatus(StrEnum):
    """Estat de verificacio documental. NO barreja informacio temporal."""

    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"


class TemporalStatus(StrEnum):
    """Estat derivat de `expires_at`. Mai es persisteix."""

    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class RegistrationType(StrEnum):
    ROLECE = "rolece"
    RELI = "reli"
    BUSINESS_CLASSIFICATION = "business_classification"
    PROFESSIONAL_REGISTRY = "professional_registry"
    ADMINISTRATIVE_ACCREDITATION = "administrative_accreditation"
    #: Registre sectorial d'operadors (p. ex. el Registre d'Operadors de la CNMC);
    #: l'emissor es a `issuer` i els serveis inscrits a `scope_service_lines` (0052).
    SECTOR_OPERATOR_REGISTRY = "sector_operator_registry"
    OTHER = "other"


class InsuranceType(StrEnum):
    CIVIL_LIABILITY = "civil_liability"
    PROFESSIONAL_INDEMNITY = "professional_indemnity"
    WORKERS_COMPENSATION = "workers_compensation"
    SURETY_BOND = "surety_bond"
    ALL_RISK = "all_risk"
    OTHER = "other"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    PARTIALLY_AVAILABLE = "partially_available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class QualificationType(StrEnum):
    DEGREE = "degree"
    PROFESSIONAL_CERTIFICATION = "professional_certification"
    TRAINING = "training"
    AUTHORIZATION = "authorization"
    OTHER = "other"


class ClientType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    MIXED = "mixed"
    OTHER = "other"


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DocumentCategory(StrEnum):
    CERTIFICATION = "certification"
    INSURANCE = "insurance"
    REGISTRATION = "registration"
    PROJECT_EVIDENCE = "project_evidence"
    FINANCIAL = "financial"
    LEGAL = "legal"
    CV = "cv"
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    OTHER = "other"


class LinkedEntityType(StrEnum):
    """Tipus d'entitat que pot ser justificada per un document.

    Ampliar aquesta llista (p. ex. amb `tender` o `requirement`) no requereix
    canvis d'esquema, nomes una migracio del CHECK constraint.
    """

    COMPANY = "company"
    COMPANY_SERVICE = "company_service"
    COMPANY_FINANCIAL_YEAR = "company_financial_year"
    COMPANY_CERTIFICATION = "company_certification"
    COMPANY_REGISTRATION = "company_registration"
    COMPANY_INSURANCE = "company_insurance"
    COMPANY_PERSON = "company_person"
    PERSON_QUALIFICATION = "person_qualification"
    COMPANY_PROJECT = "company_project"
    # Coneixement de l'empresa (fase 2 del motor): l'evidencia que permet
    # verificar-lo. Cap valor passa dels 22 caracters de la columna.
    COMPANY_OPERATING_FACT = "company_operating_fact"
    COMPANY_SOLUTION = "company_solution"
    SOLUTION_CAPABILITY = "solution_capability"
    COMPANY_PRODUCT = "company_product"
    SOLUTION_PRODUCT = "solution_product"
    COMPANY_PARTNER = "company_partner"
    PARTNER_ROLE = "partner_role"
    # Mitjans operatius (0052): equips i instruments que l'empresa pot adscriure.
    COMPANY_EQUIPMENT = "company_equipment"


class RelationshipType(StrEnum):
    """Que aporta el document respecte de l'entitat enllacada."""

    CERTIFICATE = "certificate"
    CERTIFICATE_OF_GOOD_EXECUTION = "certificate_of_good_execution"
    POLICY = "policy"
    CONTRACT = "contract"
    INVOICE = "invoice"
    FINANCIAL_STATEMENT = "financial_statement"
    CV = "cv"
    DIPLOMA = "diploma"
    ACCREDITATION = "accreditation"
    TECHNICAL_SHEET = "technical_sheet"
    REFERENCE_LETTER = "reference_letter"
    SUPPORTING_DOCUMENT = "supporting_document"
    OTHER = "other"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    #: Deixar de comptar sense esborrar (C-9). No es `DELETE`: la fila es queda,
    #: i dir-ne esborrat al registre seria descriure malament el que va passar.
    #: No canvia l'amplada de la columna, que la fixa `login_failed`.
    ARCHIVE = "archive"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    DOWNLOAD = "download"
    #: Paquet documental public (3B-2 §2.2, §2.4): l'operador verifica, retira o
    #: restaura un document oficial aportat. Tampoc canvien l'amplada de la columna
    #: (VARCHAR sense CHECK, `native_enum=False`): cap migracio.
    VERIFY = "verify"
    RETIRE = "retire"
    RESTORE = "restore"


# ---------------------------------------------------------------------------
# Ingestio de fonts externes
# ---------------------------------------------------------------------------


class SourceType(StrEnum):
    SYNDICATION_ATOM = "syndication_atom"
    API = "api"
    FILE = "file"
    #: Licitacions introduides per una persona. La font segueix sent provinenca:
    #: qui pot veure la licitacio ho diu `Tender.owner_company_id`, mai la font.
    MANUAL = "manual"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    #: Mateix `updated` que l'estat canonic i hash diferent: es conserven tots dos
    #: `SourceRecord`, no s'aplica cap (0055, radar 3B-1).
    CONFLICT = "conflict"


# ---------------------------------------------------------------------------
# Model canonic de licitacions
#
# Els codis de la font es tradueixen a aquests valors. Sempre hi ha `UNKNOWN` i
# `OTHER`: preferim una dada buida i honesta a una d'inventada.
# ---------------------------------------------------------------------------


class TenderStatus(StrEnum):
    PRE_ANNOUNCEMENT = "pre_announcement"   # PLACSP: PRE
    OPEN = "open"                           # PLACSP: PUB (en termini)
    PENDING_AWARD = "pending_award"         # PLACSP: EV
    AWARDED = "awarded"                     # PLACSP: ADJ
    RESOLVED = "resolved"                   # PLACSP: RES
    CANCELLED = "cancelled"                 # PLACSP: ANUL
    UNKNOWN = "unknown"


class ProcedureType(StrEnum):
    OPEN = "open"
    OPEN_SIMPLIFIED = "open_simplified"
    RESTRICTED = "restricted"
    NEGOTIATED_WITHOUT_PUBLICITY = "negotiated_without_publicity"
    NEGOTIATED_WITH_PUBLICITY = "negotiated_with_publicity"
    COMPETITIVE_DIALOGUE = "competitive_dialogue"
    MINOR_CONTRACT = "minor_contract"
    FRAMEWORK_DERIVED = "framework_derived"
    DESIGN_CONTEST = "design_contest"
    INNOVATION_PARTNERSHIP = "innovation_partnership"
    INNOVATION_PARTNERSHIP_DERIVED = "innovation_partnership_derived"
    DYNAMIC_PURCHASING_SYSTEM = "dynamic_purchasing_system"
    TENDER_WITH_NEGOTIATION = "tender_with_negotiation"
    INTERNAL_RULES = "internal_rules"
    OTHER = "other"
    UNKNOWN = "unknown"


class ContractType(StrEnum):
    GOODS = "goods"
    SERVICES = "services"
    WORKS = "works"
    PUBLIC_SERVICES_MANAGEMENT = "public_services_management"
    SERVICES_CONCESSION = "services_concession"
    PUBLIC_WORKS_CONCESSION = "public_works_concession"
    WORKS_CONCESSION = "works_concession"
    PUBLIC_PRIVATE_PARTNERSHIP = "public_private_partnership"
    SPECIAL_ADMINISTRATIVE = "special_administrative"
    PRIVATE = "private"
    PATRIMONIAL = "patrimonial"
    OTHER = "other"
    UNKNOWN = "unknown"


class TenderDocumentType(StrEnum):
    ADMINISTRATIVE_TERMS = "administrative_terms"   # plec de clausules administratives
    TECHNICAL_TERMS = "technical_terms"             # plec de prescripcions tecniques
    NOTICE = "notice"                               # anunci de licitacio
    AWARD = "award"                                 # acta o resolucio d'adjudicacio
    ADDITIONAL = "additional"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Motor d'analisi, fase 1. Dues capes que no s'han de barrejar:
#
# * RECEPCIO: com han arribat uns documents (per quin canal, que se n'ha acceptat
#   i que no). No sap res de rols.
# * CLASSIFICACIO: que es cada document de la licitacio, vingui d'on vingui.
# ---------------------------------------------------------------------------


class ReceptionChannel(StrEnum):
    """Per on han arribat els documents. Un canal nou es un adaptador nou.

    Previstos i encara no implementats: Google Drive, plataformes de
    contractacio, API i altres connectors. S'afegiran aqui (i al CHECK) quan
    existeixin, no abans.
    """

    ZIP_UPLOAD = "zip_upload"


class ReceptionStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    #: Hi ha hagut un error despres d'acceptar l'entrega. Els documents que ja
    #: s'havien creat es conserven; la recepcio ho diu en comptes d'amagar-ho.
    FAILED = "failed"


class ReceptionItemStatus(StrEnum):
    ACCEPTED = "accepted"
    SKIPPED = "skipped"
    #: Un contenidor (un ZIP dins del ZIP) que s'ha obert: els seus fitxers son
    #: elements propis.
    EXPANDED = "expanded"


class ReceptionSkipReason(StrEnum):
    SYSTEM_FILE = "system_file"            # __MACOSX, .DS_Store, ~$temporal...
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    TYPE_MISMATCH = "type_mismatch"        # els bytes contradiuen l'extensio
    NESTED_TOO_DEEP = "nested_too_deep"
    UNREADABLE_ARCHIVE = "unreadable_archive"  # ZIP intern corrupte


class DocumentRole(StrEnum):
    """Que es un document dins d'un expedient. Mes fi que `TenderDocumentType`.

    Taxonomia tancada: el classificador no pot inventar rols.
    """

    PCAP = "pcap"
    PPT = "ppt"
    CHARACTERISTICS_TABLE = "characteristics_table"
    OFFER_MODEL = "offer_model"
    INVENTORY_ANNEX = "inventory_annex"
    JUSTIFICATION_REPORT = "justification_report"
    APPROVAL_RESOLUTION = "approval_resolution"
    ADMINISTRATIVE_FORM = "administrative_form"
    NOTICE = "notice"
    OTHER = "other"
    #: Aclariments oficials de l'organ: les preguntes i respostes publicades (0065, Bloc 2).
    CLARIFICATION = "clarification"


class RoleConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RoleSource(StrEnum):
    """D'on surt el rol d'un document. No es cap columna: nomes lectura.

    Els quatre ultims son del resolutor de rols efectius (3B-2 §3.2): `person`
    hi es l'override de l'empresa (mateixa versio del fitxer), `operator` la
    revisio global de l'operador, `global_classifier` el suggeriment de la
    classificacio global vigent (confianca alta, no duplicat) i `feed` el
    `document_type` de la font. `stale_override` diu que l'empresa havia fixat un
    rol per a una versio ANTERIOR del fitxer: s'ignora i mana la capa seguent.
    """

    CLASSIFIER = "classifier"
    PERSON = "person"
    OPERATOR = "operator"
    GLOBAL_CLASSIFIER = "global_classifier"
    FEED = "feed"
    STALE_OVERRIDE = "stale_override"


class ClassificationTrigger(StrEnum):
    #: Han arribat documents nous (per qualsevol canal).
    RECEPTION = "reception"
    #: Algu ho ha demanat (per exemple, despres d'una pujada individual).
    REQUEST = "request"


class ClassificationFindingCode(StrEnum):
    TITLE_CONTENT_MISMATCH = "title_content_mismatch"
    DUPLICATE_BYTES = "duplicate_bytes"
    DUPLICATE_TEXT = "duplicate_text"
    DUPLICATE_INTERNAL_CODE = "duplicate_internal_code"
    REFERENCED_DOCUMENT_MISSING = "referenced_document_missing"
    REQUIRED_ROLE_MISSING = "required_role_missing"
    NO_TEXT = "no_text"
    FORMAT_NOT_ANALYZABLE = "format_not_analyzable"
    ROLE_UNDETERMINED = "role_undetermined"


class FindingSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# ---------------------------------------------------------------------------
# Pre-fit: encaix calculat NOMES amb metadades publiques i preferencies de
# l'empresa. No es una validacio del plec ni un Go/No-Go.
# ---------------------------------------------------------------------------


class MatchStatus(StrEnum):
    """Estat d'un pre-fit.

    Deliberadament NO existeixen GO/NO_GO: encara no s'han llegit els plecs.
    """

    CANDIDATE = "candidate"                  # val la pena revisar-la
    LOW_FIT = "low_fit"                      # encaixa poc segons metadades
    EXCLUDED = "excluded"                    # descartada (negoci o estat)
    CLOSED = "closed"                        # ja no es accionable
    INSUFFICIENT_DATA = "insufficient_data"  # no hi ha prou metadades per opinar


class ExclusionKind(StrEnum):
    """D'on ve una exclusio. Els blockers LEGALS encara no existeixen."""

    BUSINESS = "business"  # decisio de l'empresa (CPV exclos, territori, capacitat)
    STATUS = "status"      # la licitacio ja no es accionable (termini, adjudicada)


class DimensionResult(StrEnum):
    """Resultat d'una dimensio.

    UNKNOWN NO es NO_MATCH: que ens falti una dada no vol dir que no encaixi.
    """

    MATCH = "match"
    PARTIAL = "partial"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


class DownloadStatus(StrEnum):
    """Estat de la descarrega d'una versio de document."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class DocumentFileOrigin(StrEnum):
    """D'on ha sortit una versio de document.

    `FETCHED` es el que fa la ingesta sindicada: hi ha una URL d'origen i algu
    l'ha baixada. `UPLOADED` es el que fara la ingesta manual: no hi ha URL, hi
    ha una persona que ha pujat el fitxer. Sense aquesta distincio, un fitxer
    sense `source_url` no es podria distingir d'una descarrega feta malament.
    """

    FETCHED = "fetched"
    UPLOADED = "uploaded"


class ExtractionStatus(StrEnum):
    """Estat d'una passada de parser sobre un blob."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    # El format no admet extraccio de text (XML, ZIP...). No es un error.
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    FAILED = "failed"


class ExtractionQuality(StrEnum):
    """Mesura determinista, no un score de confianca."""

    GOOD = "good"
    LOW_TEXT = "low_text"
    EMPTY = "empty"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class DimensionStatus(StrEnum):
    """En quin punt esta el valor de treball d'una dimensio.

    `UNDECIDED` NO es `DECIDED`: vol dir que ningu no ha dit encara quin numero
    fara servir la candidatura, i tractar-ho com si fos decidit seria justament
    l'error que aquesta taula existeix per evitar.
    """

    #: Hi ha observacions, o no n'hi ha cap, pero ningu no ha fixat el valor.
    UNDECIDED = "undecided"
    #: Una persona ha triat un valor i ha escrit per que.
    DECIDED = "decided"
    #: Dues fonts diuen numeros diferents. Ho detecta el sistema; el resol algu.
    DISPUTED = "disputed"


class ObservationSourceKind(StrEnum):
    """QUI afirma un valor. No es la provinenca documental.

    La provinenca —quins bytes, quina versio, quina pagina— viu a
    `TenderSourceRef`. Aixo diu d'on ve l'afirmacio: del plec, d'un proveidor,
    d'un calcul nostre. Un proveidor pot dir un numero per telefon i seguir
    sent una font legitima que no te cap document al darrere.
    """

    TENDER_DOCUMENT = "tender_document"
    SUPPLIER = "supplier"
    INTERNAL = "internal"
    OTHER = "other"


class SourceRefKind(StrEnum):
    """A que s'ancora una cita.

    Son dues coses diferents i no una de sola amb camps opcionals: d'un PDF
    llegit se'n pot citar la pagina exacta, i d'un XLSX guardat nomes se'n pot
    dir on mirar. Fer veure que un full de calcul te pagines seria inventar-se
    una precisio que no tenim.
    """

    #: Una pagina concreta d'una extraccio concreta. La cita mes forta.
    EXTRACTED_PAGE = "extracted_page"
    #: Una versio concreta d'un fitxer, sense text. `location_label` diu on.
    DOCUMENT_FILE = "document_file"


class PageExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    EMPTY = "empty"
    FAILED = "failed"


class AnalysisRunStatus(StrEnum):
    """Estat d'una passada d'extraccio de requisits sobre una licitacio."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    # Part dels chunks han fallat pero n'hi ha resultats aprofitables.
    PARTIAL = "partial"
    FAILED = "failed"
    # 3B-2 §6.3 (0061): un run REAL acabat sense cap requisit ni criteri sobre
    # >= 5 pagines. No es promou mai a vigent i porta `needs_review`: o el plec
    # no en diu res, o l'extraccio ha fallat en silenci, i aixo ho decideix una
    # persona.
    COMPLETED_EMPTY = "completed_empty"


#: Pagines analitzades a partir de les quals un run real buit es sospitos.
COMPLETED_EMPTY_MIN_PAGES = 5


class RequirementRunBasis(StrEnum):
    """Sobre que s'ha construit un run de requisits (0061, 3B-2 §6.3).

    Es desa al run (columna NOT NULL) i es deriva del proveidor que el va
    respondre. Nomes un run `real` pot passar a vigent (`is_current`): una
    simulacio te la forma d'una lectura del plec, pero no ho es.
    """

    REAL = "real"
    #: `fake` o `fixture`: sortida fabricada per a desenvolupament o proves.
    SIMULATION = "simulation"
    #: Cap proveidor configurat (nom buit).
    UNCONFIGURED = "unconfigured"
    #: El carregador d'avaluacio (`gold` o `gold:*`): dades d'oracle, no un plec llegit.
    GOLD = "gold"

    @classmethod
    def for_provider(cls, name: str | None) -> RequirementRunBasis:
        """La base d'un run pel nom del seu proveidor. Pura; la mateixa regla que el
        backfill de 0061."""
        chosen = (name or "").strip().lower()
        if not chosen:
            return cls.UNCONFIGURED
        if chosen in {"fake", "fixture"}:
            return cls.SIMULATION
        if chosen == "gold" or chosen.startswith("gold:"):
            return cls.GOLD
        return cls.REAL


class LlmBudgetScope(StrEnum):
    """A que s'aplica un cap de despesa d'LLM (0061, 3B-2 §6.7, decisio 5)."""

    #: Una prova de pocs chunks (`only_chunks`): `LLM_BUDGET_CANARY_USD`.
    CANARY = "canary"
    #: Una analisi sencera: `LLM_BUDGET_RUN_USD`.
    RUN = "run"
    #: Tot el que s'ha gastat (el llibre sencer): `LLM_BUDGET_PILOT_TOTAL_USD`.
    PILOT_TOTAL = "pilot_total"


class RequirementCategory(StrEnum):
    """Taxonomia tancada. El model NO pot inventar categories."""

    DEADLINE = "deadline"
    ECONOMIC_SOLVENCY = "economic_solvency"
    TECHNICAL_SOLVENCY = "technical_solvency"
    EXPERIENCE = "experience"
    CERTIFICATION = "certification"
    REGISTRATION = "registration"
    BUSINESS_CLASSIFICATION = "business_classification"
    INSURANCE = "insurance"
    PERSONNEL = "personnel"
    PROFESSIONAL_QUALIFICATION = "professional_qualification"
    EQUIPMENT_OR_RESOURCES = "equipment_or_resources"
    ADMINISTRATIVE_DOCUMENTATION = "administrative_documentation"
    TECHNICAL_REQUIREMENT = "technical_requirement"
    CONTRACT_CONDITION = "contract_condition"
    OTHER = "other"


class RequirementObligation(StrEnum):
    """Que fa el plec amb aquest requisit, no si l'empresa el compleix.

    `MANDATORY` NO vol dir hard blocker: aixo nomes es pot decidir comparant-lo
    amb el perfil de l'empresa, i aixo es una altra iteracio.
    """

    MANDATORY = "mandatory"
    # "Se valorara": dona punts, no exclou.
    SCORED = "scored"
    INFORMATIVE = "informative"
    UNKNOWN = "unknown"


class RequirementOperator(StrEnum):
    """Relacio entre el valor exigit i el de l'empresa. No tots en tenen."""

    GTE = "gte"
    GT = "gt"
    LTE = "lte"
    LT = "lt"
    EQ = "eq"
    ONE_OF = "one_of"
    ALL_OF = "all_of"
    CONTAINS = "contains"
    EXISTS = "exists"
    DATE_BEFORE = "date_before"
    DATE_AFTER = "date_after"
    UNKNOWN = "unknown"


class RequirementScope(StrEnum):
    """A que aplica el requisit dins de l'expedient."""

    TENDER = "tender"
    LOT = "lot"
    MULTIPLE_LOTS = "multiple_lots"
    UNKNOWN = "unknown"


class ExtractionConfidence(StrEnum):
    """Confianca declarada pel model. NO es una probabilitat calibrada."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RequirementOrigin(StrEnum):
    """Qui ha escrit aquest requisit.

    No es un detall de metadata: canvia que se'n pot afirmar. Un requisit
    EXTRET el va treure un model d'uns documents concrets i te una run al
    darrere que diu quins; un de MANUAL l'ha escrit una persona i no te run ni
    en pot tenir. Barrejar-los obligaria a inventar una execucio d'analisi que
    no ha existit mai (D-09).
    """

    EXTRACTED = "extracted"
    MANUAL = "manual"


class ReviewStatus(StrEnum):
    """Revisio humana. L'output original del model no es sobreescriu mai."""

    UNREVIEWED = "unreviewed"
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class EvidenceRole(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class UncertaintyScope(StrEnum):
    """Per a que es material una incertesa del plec (perfil `product-v2`, 0062).

    Vocabulari tancat: una clausula indeterminada que no toca cap d'aquests ambits
    no es una incertesa material i no es registra.
    """

    ADMISSIBILITY = "admissibility"
    SOLVENCY = "solvency"
    CONTRACT_OBLIGATION = "contract_obligation"
    SCORING = "scoring"
    SIZING = "sizing"
    COST = "cost"
    SUBMISSION_DOCUMENTATION = "submission_documentation"
    DEADLINE = "deadline"
    LIABILITY = "liability"


class UncertaintyQuoteStatus(StrEnum):
    """Que se n'ha fet de la cita d'una incertesa.

    Nomes una cita LITERAL a la pagina declarada es desa com a evidencia; la resta
    no es desa mai (una parafrasi no es evidencia), pero la incertesa es conserva.
    """

    LITERAL = "literal"
    NOT_PROVIDED = "not_provided"
    REJECTED = "rejected"


class AwardEvaluationType(StrEnum):
    """Com es puntua un criteri d'adjudicacio."""

    AUTOMATIC = "automatic"
    JUDGMENT = "judgment"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ComplianceRunStatus(StrEnum):
    """Estat d'un calcul de compliment empresa <-> requisits."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AssessmentResult(StrEnum):
    """Resultat d'avaluar UN requisit contra el perfil d'una empresa.

    La distincio entre `DOES_NOT_SATISFY` i `UNKNOWN` es la mes important de
    tota la capa: la primera diu que les dades demostren un incompliment, la
    segona que no en tenim prou. Confondre-les fabrica NO-GOs falsos.
    """

    SATISFIES = "satisfies"
    DOES_NOT_SATISFY = "does_not_satisfy"
    #: No hi ha prou dada corporativa per decidir.
    UNKNOWN = "unknown"
    #: La regla no es prou determinista; cal judici huma.
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


class AssessmentMode(StrEnum):
    """Si la regla ha pogut decidir sola o demana revisio."""

    AUTOMATIC = "automatic"
    REVIEW_REQUIRED = "review_required"


class BlockerStatus(StrEnum):
    """Fins a quin punt un incompliment obligatori esta confirmat.

    Mentre la qualitat de l'extractor real no estigui mesurada, un requisit
    automatic no revisat NOMES pot generar un blocador `POTENTIAL`. Un NO-GO
    definitiu a partir d'una extraccio no verificada seria un fals negatiu amb
    conseqüencies comercials.
    """

    NONE = "none"
    POTENTIAL = "potential"
    CONFIRMED = "confirmed"


class EvidenceStrength(StrEnum):
    """Prova documental darrere d'una dada corporativa. No es una puntuacio."""

    STRONG = "strong"
    SUPPORTING = "supporting"
    MISSING = "missing"


class EligibilityResult(StrEnum):
    """Podem acreditar que complim? NO es "ens interessa" ni "guanyarem".

    `UNDETERMINED` no es un quart grau de compliment: es l'abscencia de
    veredicte. Zero requisits avaluats vol dir zero informacio, no zero
    bloquejadors, i son coses oposades. Presentar-ho com a `ELIGIBLE` deia a
    l'usuari "podeu acreditar que compliu" sense haver mirat ni un sol plec.
    """

    ELIGIBLE = "eligible"
    REVIEW_REQUIRED = "review_required"
    NO_GO = "no_go"
    #: No s'ha pogut avaluar res. Mai es tradueix a `NO_GO`: no saber si una
    #: empresa compleix no es el mateix que saber que no compleix.
    UNDETERMINED = "undetermined"


class RecommendationResult(StrEnum):
    """Recomanacio de negoci. Combina elegibilitat i pre-fit SENSE fusionar-los."""

    GO_CANDIDATE = "go_candidate"
    ELIGIBLE_LOW_FIT = "eligible_low_fit"
    REVIEW = "review"
    NO_GO = "no_go"
    #: Sense elegibilitat no hi ha res a recomanar. El pre-fit sol no basta: diu
    #: si la licitacio interessa, no si s'hi pot anar.
    UNDETERMINED = "undetermined"


#: Proveidors que NO llegeixen cap plec. El nom ja es a cada run, aixi que la
#: base d'una analisi es dedueix del que es va desar i no cal cap columna nova
#: que algu hagi de mantenir sincronitzada.
#:
#: Van fora de l'enum a proposit: dins d'un `str, Enum` qualsevol assignacio es
#: converteix en un membre mes, i comparar contra el seu `.value` faria coses
#: com ara donar per bo que la cadena buida es a "fixture".
SIMULATED_PROVIDERS: frozenset[str] = frozenset({"fixture"})
UNCONFIGURED_PROVIDERS: frozenset[str] = frozenset({"fake"})


class AnalysisBasis(StrEnum):
    """Sobre QUE s'ha construit una analisi. No es el seu estat.

    `status = completed` nomes diu que el proces ha acabat. Aixo diu si el que
    hi ha darrere es una lectura real del plec o una simulacio de
    desenvolupament, i son coses que l'usuari no pot confondre: una analisi
    "Completa" amb zero requisits sortida d'un proveidor fals es una afirmacio
    falsa sobre el plec.
    """

    #: Proveidor d'inferencia real.
    REAL = "real"
    #: Sortida fabricada per a desenvolupament o proves. Mai es una lectura del
    #: plec, encara que en tingui la forma.
    SIMULATION = "simulation"
    #: No hi ha cap motor configurat. El run existeix, pero no ha analitzat res.
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"

    @classmethod
    def for_provider(cls, name: str | None) -> AnalysisBasis:
        """Sobre que s'ha construit una analisi, segons qui la va respondre.

        Viu aqui i no a `app.ai` a proposit: qui ho ha de saber es el domini
        —el paquet de presentacio, el workspace, l'esquema de l'API— i cap
        d'ells no pot dependre de la capa d'inferencia. Es un mapatge de noms,
        no una crida a res.

        `fake` es el doble de proves: no encua res per defecte i respon buit. Si
        apareix a un run que veu un usuari, el que ha passat no es que el plec
        no tingues requisits: es que no hi ha cap motor configurat.
        """
        chosen = (name or "").strip().lower()
        if chosen in SIMULATED_PROVIDERS:
            return cls.SIMULATION
        if not chosen or chosen in UNCONFIGURED_PROVIDERS:
            return cls.PROVIDER_NOT_CONFIGURED
        return cls.REAL

    @property
    def is_trustworthy(self) -> bool:
        """Nomes una analisi real sosté una afirmacio sobre el plec."""
        return self is AnalysisBasis.REAL


class CompanyEvidenceKind(StrEnum):
    """Quina dada corporativa sosté un assessment."""

    FINANCIAL_YEAR = "financial_year"
    CERTIFICATION = "certification"
    REGISTRATION = "registration"
    INSURANCE = "insurance"
    PROJECT = "project"
    PERSON = "person"
    QUALIFICATION = "qualification"
    DOCUMENT = "document"


class WorkspaceStatus(StrEnum):
    """On es troba una candidatura.

    `GO_APPROVED`, `NO_GO_APPROVED`, `APPROVED_FOR_SUBMISSION` i `SUBMITTED` NO
    els pot escriure cap regla
    automatica: son consequencia d'una accio humana registrada. La resta descriu
    el treball, no una decisio.
    """

    ANALYSIS = "analysis"
    NEEDS_REVIEW = "needs_review"
    AWAITING_DECISION = "awaiting_decision"
    GO_APPROVED = "go_approved"
    NO_GO_APPROVED = "no_go_approved"
    PREPARING_BID = "preparing_bid"
    READY_FOR_FINAL_REVIEW = "ready_for_final_review"
    #: Una persona ha aprovat un paquet CONCRET per presentar. Encara no s'ha
    #: presentat res: aixo es una altra cosa i una altra iteracio.
    APPROVED_FOR_SUBMISSION = "approved_for_submission"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"


class WorkspaceDecisionType(StrEnum):
    """La decisio humana. Mai la pren el sistema."""

    GO = "go"
    NO_GO = "no_go"
    DEFER = "defer"


class ChecklistItemStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class ChecklistItemSource(StrEnum):
    """D'on surt l'item. Els humans no es regeneren."""

    REQUIREMENT = "requirement"
    MANUAL = "manual"


class WorkspaceTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EvidenceItemStatus(StrEnum):
    """Estat d'una peca del dossier documental.

    `SUGGESTED` NO vol dir "el presentarem": vol dir que el motor de compliment
    ja va fer servir aquest document i que per tant es un candidat. Nomes una
    persona el converteix en `SELECTED`.

    NO hi ha cap estat `expired`: la caducitat es deriva del document i del
    termini (`DocumentValidity`) i aquella es l'unica font de veritat. Un estat
    manual paral·lel es podria quedar enrere i dir el contrari que les dates.
    """

    SUGGESTED = "suggested"
    SELECTED = "selected"
    MISSING = "missing"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


class EvidenceItemSource(StrEnum):
    """D'on surt la peca: derivada del compliment o posada a ma."""

    COMPLIANCE = "compliance"
    MANUAL = "manual"


class DocumentValidity(StrEnum):
    """Vigencia d'un document RESPECTE D'AQUESTA LICITACIO.

    `EXPIRES_BEFORE_DEADLINE` es el cas que costa mes de veure sol: el document
    es vigent avui i haura caducat el dia que s'ha de presentar l'oferta.
    """

    VALID = "valid"
    EXPIRES_BEFORE_DEADLINE = "expires_before_deadline"
    EXPIRED = "expired"
    NO_EXPIRY = "no_expiry"
    NO_DOCUMENT = "no_document"


class WorkspaceReadiness(StrEnum):
    """Si el DOSSIER esta a punt. No es el compliment ni la decisio.

    `READY_FOR_FINAL_REVIEW` vol dir "sembla complet per fer la revisio humana
    final". No vol dir `submitted` ni s'hi acosta.
    """

    NOT_STARTED = "not_started"
    INCOMPLETE = "incomplete"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_FINAL_REVIEW = "ready_for_final_review"


class DossierCoverage(StrEnum):
    """Cobertura documental d'un requisit. NO es el resultat de compliment.

    Es pot complir un requisit i no tenir-ne encara el PDF: `satisfies` amb
    `MISSING_DOCUMENT` es un estat perfectament normal i important de veure.
    """

    COVERED = "covered"
    MISSING_DOCUMENT = "missing_document"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


class SubmissionPackageStatus(StrEnum):
    """Estat d'un paquet de presentacio.

    `DRAFT` es transitori: nomes existeix mentre es construeix el paquet dins
    d'una transaccio. A partir de `FROZEN` la fotografia es immutable; canviar
    qualsevol peca vol dir crear una revisio nova, no editar aquesta.
    """

    DRAFT = "draft"
    FROZEN = "frozen"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class PackageApprovalDecision(StrEnum):
    """El veredicte d'una persona sobre UN paquet concret."""

    APPROVED_FOR_SUBMISSION = "approved_for_submission"
    REJECTED = "rejected"


class RequirementModality(StrEnum):
    """Com obliga el plec, amb mes matis que `obligation`.

    Existeix a banda i no substituint `RequirementObligation` perque 4C llegeix
    l'obligatorietat i canviar-ne el significat trencaria tot el compliment ja
    calculat. Aqui hi va el matis; alla, la consequencia conservadora.

    `ALTERNATIVE` i `CONDITIONAL` NO poden derivar en `mandatory`: una de dues
    vies d'acreditacio no es una exigencia, i una condicio que potser no s'activa
    tampoc. Convertir-les en obligatories fabricaria blocadors falsos.
    """

    MANDATORY = "mandatory"
    OPTIONAL = "optional"
    #: Una de diverses vies possibles. Va acompanyada d'`alternative_group`.
    ALTERNATIVE = "alternative"
    #: Nomes obliga si es dona un supost ("en su caso", "si procede").
    CONDITIONAL = "conditional"
    INFORMATIONAL = "informational"
    UNKNOWN = "unknown"


class EconomicMetric(StrEnum):
    """Que es mesura a un requisit de solvencia economica."""

    ANNUAL_TURNOVER = "annual_turnover"
    #: Volum de negoci NOMES en l'ambit del contracte.
    SPECIFIC_TURNOVER = "specific_turnover"
    NET_WORTH = "net_worth"
    WORKING_CAPITAL = "working_capital"
    RATIO = "ratio"
    OTHER = "other"


class ReferencePeriodType(StrEnum):
    LAST_N_YEARS = "last_n_years"
    FISCAL_YEAR = "fiscal_year"
    CONTRACT_DURATION = "contract_duration"
    EXPLICIT_RANGE = "explicit_range"
    UNKNOWN = "unknown"


class AggregationType(StrEnum):
    """Com es combinen els anys del periode de referencia.

    La distincio que el gold huma va trobar a faltar: "cada un dels tres ultims
    anys" i "el de major volum dels tres ultims" son requisits diferents, i
    representar-los tots dos com `years: 3` els fa indistingibles.

    `UNKNOWN` es el valor per defecte a proposit: si el text no ho diu, no
    s'endevina.
    """

    #: Tots i cadascun dels anys han de superar el llindar.
    EACH_YEAR = "each_year"
    #: N'hi ha prou amb un any qualsevol.
    ANY_YEAR = "any_year"
    #: El millor/major any del periode.
    MAX = "max"
    AVERAGE = "average"
    #: La suma del periode.
    CUMULATIVE = "cumulative"
    UNKNOWN = "unknown"


class PercentageBase(StrEnum):
    """Sobre que es calcula un percentatge. Sense base, un % no vol dir res."""

    CONTRACT_VALUE = "contract_value"
    #: Pressupost anual mitja del contracte ("anualidad media").
    CONTRACT_ANNUITY = "contract_annuity"
    BASE_BUDGET = "base_budget"
    ESTIMATED_VALUE = "estimated_value"
    LOT_VALUE = "lot_value"
    OTHER = "other"
    UNKNOWN = "unknown"


class InsuranceLimitKind(StrEnum):
    """Quin limit d'una polissa expressa un import.

    Cobertura total i sublimit per victima son condicions DIFERENTS sobre la
    mateixa polissa. Fusionar-les perdria la que sigui mes restrictiva.
    """

    TOTAL = "total"
    PER_CLAIM = "per_claim"
    PER_VICTIM = "per_victim"
    ANNUAL_AGGREGATE = "annual_aggregate"
    #: RC patronal per victima (0052): diferent del sublimit general per victima.
    EMPLOYER_PER_VICTIM = "employer_per_victim"
    SUBLIMIT = "sublimit"
    UNKNOWN = "unknown"


class ProofType(StrEnum):
    """Amb quin document s'acredita. Es la FORMA, no el requisit."""

    CERTIFICATE = "certificate"
    CERTIFICATE_OF_GOOD_EXECUTION = "certificate_of_good_execution"
    INSURANCE_POLICY = "insurance_policy"
    INSURANCE_CERTIFICATE = "insurance_certificate"
    FINANCIAL_STATEMENT = "financial_statement"
    TAX_DECLARATION = "tax_declaration"
    REGISTRY_ENTRY = "registry_entry"
    SWORN_STATEMENT = "sworn_statement"
    OTHER = "other"
    UNKNOWN = "unknown"


class AwardCriterionType(StrEnum):
    """Com es puntua un criteri d'adjudicacio."""

    PRICE = "price"
    QUALITY = "quality"
    TECHNICAL = "technical"
    ENVIRONMENTAL = "environmental"
    SOCIAL = "social"
    DELIVERY_TIME = "delivery_time"
    WARRANTY = "warranty"
    IMPROVEMENT = "improvement"
    OTHER = "other"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Feina asincrona (Fase 7A)
#
# `WorkJob` es l'execucio tecnica global; `JobRequest`, la peticio d'un tenant.
# Una mateixa execucio pot satisfer peticions de diverses empreses.
# ---------------------------------------------------------------------------


class WorkJobType(StrEnum):
    PROCESS_TENDER_DOCUMENTS = "process_tender_documents"
    ANALYZE_TENDER_REQUIREMENTS = "analyze_tender_requirements"


class WorkJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    # Nomes anàlisi: el worker ha perdut el lease i el run s'ha de reconciliar
    # contra el seu estat real. Mai no es torna a executar sol.
    ORPHANED = "orphaned"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRequestStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobOutcome(StrEnum):
    #: S'ha fet la feina de nou.
    EXECUTED = "executed"
    #: L'execucio ha trobat un resultat vigent i no l'ha repetit.
    REUSED = "reused"
    #: La peticio ja tenia resultat en crear-se: no s'ha encuat res.
    REUSED_EXISTING = "reused_existing"
    #: La feina ha acabat, pero una part ha fallat (chunks o documents).
    PARTIAL = "partial"


class AnalysisQuoteStatus(StrEnum):
    """Estat d'un preview d'analisi que l'usuari pot confirmar."""

    OPEN = "open"
    CONFIRMED = "confirmed"
    #: Els plecs o el calcul han canviat: s'ha substituit per un preview nou.
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Solucio tecnica (Sprint 2)
#
# "Com oferirem aquest requisit?" NO es "el complim?" (aixo es
# `CompanyRequirementAssessment`) ni "amb quin paper ho acreditem?" (aixo es
# `WorkspaceEvidenceItem`). Tres preguntes, tres taules.
# ---------------------------------------------------------------------------


class TechnicalSolutionStatus(StrEnum):
    """On es una proposta de solucio.

    `VALIDATED` NOMES l'escriu una persona, amb signatura, data i motiu. Cap
    regla automatica i cap model hi arriba: es la mateixa separacio que ja
    protegeix `WorkspaceDecision` i la confirmacio de pertinenca del dossier.
    """

    NOT_STARTED = "not_started"
    DRAFTING = "drafting"
    NEEDS_VALIDATION = "needs_validation"
    VALIDATED = "validated"


class TechnicalSolutionSource(StrEnum):
    """D'on surt la fila: d'un requisit del plec o de la ma d'algu."""

    REQUIREMENT = "requirement"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Oferta economica (Sprint 2)
# ---------------------------------------------------------------------------


class PricingBasis(StrEnum):
    """Com es factura una linia de l'oferta.

    Determina NOMES quants periodes multipliquen el preu unitari; no diu res de
    com es paga ni de com es comptabilitza. La durada mai s'infereix: una linia
    `MONTHLY` o `ANNUAL` sense durada efectiva deixa els totals temporals
    indeterminats, i assumir 12 mesos seria inventar el contracte (C-3).
    """

    #: Un sol lliurament. El cost real NO depen de la durada: una inversio de
    #: 24.000 EUR a 24 mesos continua costant 24.000 EUR, i els 1.000 EUR/mes
    #: son una lectura que no entra en cap suma.
    ONE_OFF = "one_off"
    MONTHLY = "monthly"
    #: Es prorrateja: 30 mesos son 2,5 anualitats. Si un proveidor factura
    #: periodes sencers, aixo es una altra estructura i no una variant d'aqui.
    ANNUAL = "annual"
    #: La quantitat ja son les unitats de tot el contracte: no es multiplica
    #: mai per la durada.
    PER_UNIT = "per_unit"


# ---------------------------------------------------------------------------
# Opcions de solucio (Sprint 3)
# ---------------------------------------------------------------------------


class QuoteStatus(StrEnum):
    """On es la cotitzacio d'una opcio. Workflow intern, no un CRM.

    No dispara cap correu, no parla amb cap proveidor i no descarrega cap
    document: diu en quin punt es la gestio, perque la persona que prepara
    l'oferta sapiga que li falta.

    `RECEIVED` vol dir **rebuda**, no aprovada ni validada. Confondre-ho faria
    que una xifra encara per contrastar semblés tancada.
    """

    #: Encara no s'ha gestionat. Es on neix tota opcio.
    DRAFT = "draft"
    #: S'ha demanat al proveidor.
    REQUESTED = "requested"
    #: Demanada i esperant resposta.
    PENDING = "pending"
    #: Ha arribat. Rebuda, que no vol dir bona.
    RECEIVED = "received"
    #: Va arribar pero ja no es vigent.
    EXPIRED = "expired"
    #: El proveidor no cotitza, o aquesta via s'ha tancat des de l'altra banda.
    DECLINED = "declined"


class OptionSelection(StrEnum):
    """Si aquesta opcio es la que fara servir la candidatura.

    **`UNDECIDED` no es `REJECTED`.** Es la distincio que justifica que existeixi
    aquest enum en comptes d'un boolea: una alternativa que ningu no ha mirat
    encara i una que algu ha descartat amb motiu son coses diferents, i
    tractar-les igual faria desapareixer feina que no s'ha fet.
    """

    #: Ningu no ha decidit. Cap opcio neix d'una altra manera.
    UNDECIDED = "undecided"
    SELECTED = "selected"
    #: Descartada per una persona, sempre amb motiu escrit.
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Dubtes i discrepancies (Sprint 3)
# ---------------------------------------------------------------------------


class IssueType(StrEnum):
    """Quina mena de pregunta oberta es.

    Classifica per **a qui cal preguntar o que cal esbrinar**, no per gravetat:
    la urgencia ja la diuen `due_at` i `blocks_final_review`, i una taxonomia de
    risc convidaria a inventar-ne la severitat.
    """

    #: Ho hem de resoldre nosaltres.
    INTERNAL_QUESTION = "internal_question"
    #: Cal preguntar-ho a l'organ de contractacio.
    AUTHORITY_QUESTION = "authority_question"
    SUPPLIER_QUESTION = "supplier_question"
    #: Dues fonts diuen coses diferents i cal decidir quina val.
    CONTRADICTION = "contradiction"
    #: Falta una dada que encara no te ningu.
    MISSING_INFORMATION = "missing_information"


class IssueStatus(StrEnum):
    """On es la pregunta.

    `DISMISSED` **no** es `RESOLVED`: descartar vol dir que la pregunta ja no ve
    al cas, no que en tinguem la resposta. Barrejar-los faria que "ho sabem"
    inclogues coses que ningu no ha contestat mai.
    """

    OPEN = "open"
    #: Depen d'algu o d'alguna cosa de fora: no hi podem fer res ara mateix.
    WAITING = "waiting"
    #: Hi ha resposta, i esta escrita.
    RESOLVED = "resolved"
    #: Es tanca perque ja no es pertinent.
    DISMISSED = "dismissed"


# --------------------------------------------------------------------------- #
# Capa de revisio (S-R1): estat de revisio PER EMPRESA, separat de l'extraccio. #
# --------------------------------------------------------------------------- #


class ReviewItemKind(StrEnum):
    """Que es un element canonic de revisio."""

    REQUIREMENT = "requirement"
    AWARD_CRITERION = "award_criterion"


class ReviewVerification(StrEnum):
    """Que en diu el revisor. Independent de la provinenca (qui l'ha escrit).

    `PARTIAL` no es `NEEDS_REVIEW`: parcial vol dir que s'ha revisat i nomes una
    part es correcta o esta coberta; cal revisar vol dir que ningu no ho ha fet
    encara i hi ha motius per fer-ho.
    """

    UNREVIEWED = "unreviewed"
    VERIFIED = "verified"
    PARTIAL = "partial"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ReviewAwardRole(StrEnum):
    """Paper d'un criteri a l'estructura de puntuacio canonica."""

    #: Puntua i compta al total.
    SCORED = "scored"
    #: Total d'un bloc que es la suma dels seus fills: no es compta dues vegades.
    PARENT_TOTAL = "parent_total"
    #: El mateix bloc vist des del model d'oferta (caselles SI/NO).
    OFFER_FORM_VIEW = "offer_form_view"
    NOT_SCORED = "not_scored"
    DESIRABLE = "desirable"


class ReviewOriginHint(StrEnum):
    """D'on ve l'element canonic. La provinenca visible es deriva de les fonts."""

    EXTRACTION = "extraction"
    MANUAL_REVIEW = "manual_review"


class ReviewSourceRole(StrEnum):
    """Com participa una fila font en un element canonic."""

    PRIMARY = "primary"
    ABSORBED_DUPLICATE = "absorbed_duplicate"
    EXTRA_EVIDENCE = "extra_evidence"


class ReviewAction(StrEnum):
    """Una entrada de l'historial. Nomes s'hi afegeix: mai no es modifica."""

    CREATE = "create"
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"
    MARK_PARTIAL = "mark_partial"
    NOTE = "note"
    SET_STATUS = "set_status"
    SET_AWARD_ROLE = "set_award_role"
    LINK_SOURCE = "link_source"
    ARCHIVE = "archive"
    #: La font primaria passa a la fila equivalent d'una run nova (S-R9).
    RECONCILE = "reconcile"


class ReviewReconciliationState(StrEnum):
    """Cicle de vida d'una reconciliacio d'una empresa cap a una run nova."""

    #: Nomes dins de la transaccio d'aplicacio; mai no queda desat aixi.
    IN_PROGRESS = "in_progress"
    #: S'han aplicat les coincidencies exactes; queda feina per a una persona.
    NEEDS_REVIEW = "needs_review"
    #: No queda res i totes les fonts extretes apunten a la run desti.
    COMPLETED = "completed"


class ReviewRelationType(StrEnum):
    """Relacio dirigida `from -> to` entre dos elements canonics."""

    DUPLICATE_OF = "duplicate_of"
    RESTATEMENT_OF = "restatement_of"
    PARENT_OF = "parent_of"
    PARTIAL_MATCH_OF = "partial_match_of"
    SUPERSEDES = "supersedes"
    RELATED_TO = "related_to"


# ---------------------------------------------------------------------------
# Motor d'analisi, fase 2: coneixement de l'empresa.
#
# Dues naturaleses que no es barregen: l'ESTAT ACTUAL (el que l'empresa es i pot
# fer avui, amb procedencia) i l'HISTORIC (el que va fer o dir en licitacions
# passades). L'historic nomes suggereix: mai no acredita res.
# ---------------------------------------------------------------------------


class KnowledgeOrigin(StrEnum):
    """Qui afirma una dada del coneixement actual."""

    #: Una persona de l'empresa l'ha declarada.
    DECLARED = "declared"
    #: Una persona ha validat una observacio de l'historic i l'ha promoguda.
    PROMOTED_OBSERVATION = "promoted_observation"


class KnowledgeLifecycle(StrEnum):
    CURRENT = "current"
    #: En tramit (una certificacio que s'esta obtenint): no compta, genera una pregunta.
    IN_PROGRESS = "in_progress"
    #: Ja no es aixi (una solucio que s'ha deixat d'oferir): no compta.
    RETIRED = "retired"


class CapabilityValueType(StrEnum):
    BOOLEAN = "boolean"
    NUMBER = "number"
    #: Un valor d'una escala ordenada (PoE af < at < bt).
    ORDINAL = "ordinal"


class CapabilityComparator(StrEnum):
    #: Booleana: cal que estigui suportada.
    IS_TRUE = "is_true"
    GTE = "gte"
    LTE = "lte"


class OperatingFactKey(StrEnum):
    """Fets de l'empresa que avui no tenen cap altre lloc al perfil."""

    TELECOM_OPERATOR_CNMC = "telecom_operator_cnmc"
    OWN_NUMBER_RANGE_NRN = "own_number_range_nrn"
    DATACENTER_TIER3 = "datacenter_tier3"
    OWN_FTTH_INFRASTRUCTURE = "own_ftth_infrastructure"
    SUPPORT_24X7 = "support_24x7"
    #: Fet quantitatiu (0052): `value` diu si l'empresa en te i `value_number` quantes.
    #: El tipus de valor de cada clau es a `app/knowledge/vocabulary.py`.
    IPV4_ASSIGNABLE_COUNT = "ipv4_assignable_count"


class EquipmentType(StrEnum):
    """Tipus d'equip o instrument adscrivible (0052). Tancat; s'amplia aqui i a una
    migracio del CHECK. No es cap producte que l'empresa vengui."""

    OTDR = "otdr"
    OLTS = "olts"
    FUSION_SPLICER = "fusion_splicer"
    OPTICAL_POWER_METER = "optical_power_meter"
    LIGHT_SOURCE = "light_source"
    CABLE_CERTIFIER = "cable_certifier"
    SPECTRUM_ANALYZER = "spectrum_analyzer"
    OTHER = "other"


class EquipmentAccessMode(StrEnum):
    """Com l'empresa disposa de l'equip. Nul = no consta."""

    OWNED = "owned"
    LEASED = "leased"
    RENTED = "rented"
    PARTNER_PROVIDED = "partner_provided"
    OTHER = "other"


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    SUPPORTED_WITH_LIMITS = "supported_with_limits"
    NOT_SUPPORTED = "not_supported"


class DeliveryMode(StrEnum):
    OWN = "own"
    PARTNER = "partner"
    SUBCONTRACT = "subcontract"
    RESALE = "resale"


class PartnerRole(StrEnum):
    SUPPLIER = "supplier"
    WHOLESALE_OPERATOR = "wholesale_operator"
    SUBCONTRACTOR = "subcontractor"
    COMMERCIAL_PARTNER = "commercial_partner"
    BID_WRITER = "bid_writer"
    ADVISOR = "advisor"


class HistoricBidDecision(StrEnum):
    SUBMITTED = "submitted"
    #: Hi ha oferta preparada pero cap nota no confirma que es presentes.
    PREPARED = "prepared"
    NOT_SUBMITTED = "not_submitted"
    UNDECIDED = "undecided"


class HistoricBidResult(StrEnum):
    WON = "won"
    LOST = "lost"
    UNKNOWN = "unknown"


class ObservationSubject(StrEnum):
    """De que parla una observacio: a quina part del coneixement actual es podria promoure."""

    OPERATING_FACT = "operating_fact"
    CERTIFICATION = "certification"
    SERVICE = "service"
    SOLUTION = "solution"
    PRODUCT = "product"
    PARTNER = "partner"


class ObservationStatus(StrEnum):
    #: Suggeriment de l'historic. No es coneixement actual.
    CANDIDATE = "candidate"
    #: Una persona l'ha validat: n'ha nascut coneixement actual traçable.
    PROMOTED = "promoted"
    #: Una persona l'ha descartat (ja no es aixi, o no s'hi pot fer res).
    DISMISSED = "dismissed"


class CoverageCompleteness(StrEnum):
    """Si la llista d'arees de cobertura d'una dada es tota la que hi ha.

    `complete` = «aquesta es tota la cobertura actual coneguda»; `partial` =
    «algunes zones conegudes, no totes»; `unknown` = no s'ha dit res, que es
    l'estat de tot el que existia abans de la 0047. Sense `complete`, una area
    que no hi es no vol dir que no es cobreixi.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class AreaKind(StrEnum):
    """Tipus d'area de cobertura. Sense GIS ni jerarquia: un municipi i una
    comarca son coses diferents i no es comparen entre si."""

    MUNICIPALITY = "municipality"
    COMARCA = "comarca"
    PROVINCE = "province"
    REGION = "region"
    COUNTRY = "country"
    ADDRESS = "address"
    CUSTOM = "custom"


class CoverageEntityType(StrEnum):
    """Quines dades de coneixement poden portar cobertura territorial."""

    OPERATING_FACT = "operating_fact"
    SERVICE = "service"
    PARTNER_ROLE = "partner_role"


class RequirementTiming(StrEnum):
    """QUAN s'ha de complir un requisit o un criteri, segons el plec (0048).

    `at_submission`: ha d'existir o complir-se en presentar l'oferta.
    `at_execution`: pot satisfer-se durant l'execucio, segons el plec.
    `ambiguous`: el plec dona peu real a mes d'una lectura temporal.
    `unknown`: encara no ho sabem. Es el valor de tot el que hi havia abans de
    la 0048, i no se'n deriva cap resultat de compliment.
    """

    AT_SUBMISSION = "at_submission"
    #: Despres de l'adjudicacio i abans de formalitzar (assegurança, garantia definitiva).
    BEFORE_FORMALIZATION = "before_formalization"
    AT_EXECUTION = "at_execution"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class TenderSiteRole(StrEnum):
    """Que hi fa una seu del plec. Ser critica es una marca a part (`is_critical`)."""

    CENTRAL = "central"
    REMOTE = "remote"


class TerritoryMatchMode(StrEnum):
    """Com s'han de llegir els territoris d'un requisit (0049): n'hi ha prou amb un
    (`any`) o cal cobrir-los tots (`all`). Nomes s'emmagatzema: comparar-ho amb la
    cobertura d'una empresa es cosa de 0050."""

    ALL = "all"
    ANY = "any"


class TerritoryCompleteness(StrEnum):
    """Si el conjunt de territoris d'un requisit es el que el plec exigeix (0050).
    `not_applicable` = cap dimensio territorial; `complete` = totes les arees conegudes
    hi son; `partial` = n'hi ha i en falten; `unknown` = hi ha (o pot haver-hi) dimensio
    territorial i no tenim el conjunt (p. ex. un annex absent). 0051 no pot llegir mai
    «cap territori» com a «cobert»: primer mira aixo."""

    NOT_APPLICABLE = "not_applicable"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ProofKind(StrEnum):
    """Quina prova o documentacio exigeix el plec per a un requisit (0049)."""

    RESPONSIBLE_DECLARATION = "responsible_declaration"
    COVERAGE_MAP = "coverage_map"
    OWNERSHIP_DOCUMENT = "ownership_document"
    CERTIFICATE = "certificate"
    ANNUAL_ACCOUNTS = "annual_accounts"
    EXECUTION_CERTIFICATES = "execution_certificates"
    INSURANCE_POLICY = "insurance_policy"
    CALIBRATION_CERTIFICATE = "calibration_certificate"
    TECHNICAL_SHEET = "technical_sheet"
    REGISTRY_INSCRIPTION = "registry_inscription"
    STAFF_COMMITMENT = "staff_commitment"
    OTHER = "other"


class NeedType(StrEnum):
    """Quina mena de necessitat tecnica es (0049)."""

    MINIMUM = "minimum"
    SCORED_IMPROVEMENT = "scored_improvement"
    DEMO = "demo"


class NeedComparator(StrEnum):
    """Com es llegeix el valor d'una necessitat. Tancat: no es cap llenguatge de regles."""

    PRESENT = "present"
    EQ = "eq"
    GTE = "gte"
    LTE = "lte"
    CONTAINS = "contains"


class ContractFactCode(StrEnum):
    """Fets contractuals heterogenis sense columna propia (0049). El que ja te
    columna (PBL, VEC, organ, termini, durada, SARA, inici) NO hi va."""

    ENVELOPES = "envelopes"
    PAGE_LIMITS = "page_limits"
    DEFINITIVE_GUARANTEE = "definitive_guarantee"
    LIABILITY_INSURANCE = "liability_insurance"
    SUBCONTRACTING = "subcontracting"
    ABNORMAL_OFFER_PARAMS = "abnormal_offer_params"
    PENALTIES = "penalties"
    LIVE_DEMO = "live_demo"
    SITE_VISIT = "site_visit"
    VARIANTS_POLICY = "variants_policy"
    BILLING_TERMS = "billing_terms"
    MAX_MODIFICATION = "max_modification"
    REVERSIBILITY = "reversibility"
    DATA_PROTECTION = "data_protection"
    ESSENTIAL_OBLIGATIONS = "essential_obligations"


# --------------------------------------------------------------------------- #
# Vocabulari de l'analisi (0051): cada eix es independent dels altres.          #
# --------------------------------------------------------------------------- #


class AnalysisKnowledgeSource(StrEnum):
    """D'on surt la dada d'empresa que sosté una avaluacio."""

    CURRENT_VERIFIED = "current_verified"
    CURRENT_DECLARED = "current_declared"
    #: Nomes pistes historiques: donen context, mai `satisfies`.
    HISTORICAL = "historical"
    NONE = "none"
    MIXED = "mixed"
    NOT_APPLICABLE = "not_applicable"


class ProofStatus(StrEnum):
    """Si la prova exigida es pot demostrar. Independent de la verificacio."""

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class FitResult(StrEnum):
    SATISFIES = "satisfies"
    DOES_NOT_SATISFY = "does_not_satisfy"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class TerritoryVerdict(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CriterionResult(StrEnum):
    ACHIEVABLE = "achievable"
    NOT_ACHIEVABLE = "not_achievable"
    NEEDS_REVIEW = "needs_review"
    NOT_ASSESSABLE = "not_assessable"


class PointsStatus(StrEnum):
    SECURED = "secured"
    AT_RISK = "at_risk"
    LOST = "lost"
    MIXED = "mixed"
    NOT_ASSESSABLE = "not_assessable"


class AssessmentVerification(StrEnum):
    """Si la dada que sosté l'avaluacio esta verificada. NO es `FitResult`."""

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CandidateType(StrEnum):
    OWN_CURRENT = "own_current"
    PARTNER_CURRENT = "partner_current"
    SUBCONTRACT_UNKNOWN = "subcontract_unknown"
    #: Nomes des d'una hipotesi explicita; mai compta com a cobertura actual.
    OWN_FUTURE = "own_future"


class CandidateOrigin(StrEnum):
    CURRENT_KNOWLEDGE = "current_knowledge"
    HYPOTHESIS = "hypothesis"


class CandidateReadiness(StrEnum):
    AVAILABLE = "available"
    PENDING_COVERAGE = "pending_coverage"
    PENDING_PARTNER = "pending_partner"
    PENDING_VALIDATION = "pending_validation"
    UNAVAILABLE = "unavailable"


class FindingType(StrEnum):
    """Cap `blocker_confirmed`: aixo es una decisio humana, mai del motor."""

    BLOCKER_POTENTIAL = "blocker_potential"
    REVIEW_REQUIRED = "review_required"
    MISSING_INFORMATION = "missing_information"
    EVIDENCE_GAP = "evidence_gap"
    TERRITORY_GAP = "territory_gap"
    DELIVERY_GAP = "delivery_gap"
    WARNING = "warning"


class QuestionAudience(StrEnum):
    BUYER = "buyer"
    COMPANY = "company"


# ---------------------------------------------------------------------------
# Paquet documental public (0058).
# ---------------------------------------------------------------------------


class TenderDocumentOrigin(StrEnum):
    """Qui ha fet arribar un document a la licitacio."""

    FEED = "feed"  # enumerat per la font (el connector que el governa)
    OWNER_UPLOAD = "owner_upload"  # pujada/ZIP en una licitacio manual propia
    OPERATOR_URL = "operator_url"  # obtingut per l'operador d'una URL oficial
    OPERATOR_UPLOAD = "operator_upload"  # pujat per l'operador
    #: Baixat pel connector del portal oficial (PSCP), que el governa, i verificat a
    #: l'origen: l'MD5 del fitxer es el que publica el portal (0065, Bloc 2).
    PORTAL = "portal"


class DocumentContributionStatus(StrEnum):
    """Verificacio d'un document aportat per l'operador."""

    NOT_APPLICABLE = "not_applicable"  # feed, owner_upload o portal: no es verifica
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    REJECTED = "rejected"


class PackageGapCode(StrEnum):
    """Que falta al paquet documental d'una licitacio."""

    REQUIRED_ROLE_MISSING = "required_role_missing"
    REFERENCED_DOCUMENT_MISSING = "referenced_document_missing"
    NO_TEXT = "no_text"
    FORMAT_NOT_ANALYZABLE = "format_not_analyzable"
    ANNEX_MISSING = "annex_missing"
    DECLARED_BY_OPERATOR = "declared_by_operator"


class PackageGapProducer(StrEnum):
    CLASSIFICATION = "classification"
    OPERATOR = "operator"


# ---------------------------------------------------------------------------
# Classificacio global i revisio de rols (0059).
# ---------------------------------------------------------------------------


class GlobalClassificationTrigger(StrEnum):
    """Que ha provocat una passada de la classificacio global d'un tender."""

    #: El worker acaba de processar (descarregar/extreure) els documents.
    DOCUMENT_PROCESSING = "document_processing"
    #: L'operador ha verificat, retirat, restaurat o cedit un document.
    DOCUMENT_INTAKE = "document_intake"
    #: L'operador l'ha demanat explicitament.
    OPERATOR_REQUEST = "operator_request"


class RoleReviewAuthority(StrEnum):
    """Amb quina autoritat es revisa el rol global d'un document."""

    PLATFORM_OPERATOR = "platform_operator"
    #: Reservat: promocio d'una proposta d'empresa (fora d'abast de 3B-2).
    PROMOTED_FROM_COMPANY = "promoted_from_company"


# ---------------------------------------------------------------------------
# Provenance de l'estructura del plec i propostes (0060, 3B-2 §4).
# ---------------------------------------------------------------------------


class StructureOrigin(StrEnum):
    """D'on surt el valor VIGENT d'una fila d'estructura o d'abast del plec.

    Descriu l'ultim escriptor del valor (qui la va crear queda a `created_by`)."""

    #: Un productor automatic (extraccio). Cap en 3B-2 (l'LLM d'estructura es 3B-3).
    EXTRACTED = "extracted"
    #: L'operador de plataforma l'ha escrit directament, amb cita.
    OPERATOR = "operator"
    #: Una proposta immutable (amb cita) promoguda per la via interna.
    PROMOTED = "promoted"
    #: El propietari d'una licitacio oberta a ma (via del tenant).
    OWNER_MANUAL = "owner_manual"
    #: El carregador del gold d'avaluacio (font `gold:*`).
    GOLD = "gold"


class StructureAuthority(StrEnum):
    """Amb quina autoritat es va escriure el valor vigent."""

    PLATFORM_OPERATOR = "platform_operator"
    TENDER_OWNER = "tender_owner"
    #: Reservat: promocio d'una proposta d'empresa (fora d'abast de 3B-2).
    PROMOTED_FROM_COMPANY = "promoted_from_company"
    PRODUCER = "producer"


class StructureProposalProducer(StrEnum):
    """Qui proposa. `company_proposal` queda reservat (fora d'abast de 3B-2): ni
    l'enum ni el CHECK l'accepten encara."""

    OPERATOR = "operator"
    EXTRACTION = "extraction"


class StructureEntityKind(StrEnum):
    """Que proposa una proposta d'estructura: la forma del `payload_json`."""

    BLOCK = "block"
    SITE = "site"
    SCOPE_ITEM = "scope_item"
    CAPABILITY_NEED = "capability_need"
    AWARD_LINK = "award_link"
    CONTRACT_FACT = "contract_fact"
    TERRITORY = "territory"
    REQUIREMENT_TIMING = "requirement_timing"
    CRITERION_CITATION = "criterion_citation"


class StructureProposalStatus(StrEnum):
    PROPOSED = "proposed"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class TenderAmountKind(StrEnum):
    """Quin import d'un expedient (o d'un lot) recull una observacio de font externa (0063).

    Es desen tots tres: el PBL amb IVA es la dada que falta; el PBL sense IVA i el VEC
    serveixen per comprovar que la fila de la font es la mateixa licitacio.
    """

    PBL_WITH_TAX = "pbl_with_tax"
    PBL_WITHOUT_TAX = "pbl_without_tax"
    ESTIMATED_VALUE = "estimated_value"


class AmountObservationStatus(StrEnum):
    """Resultat de les guardes d'una observacio d'import (0063).

    * `accepted`: totes les guardes passen i l'import es > 0. Nomes una `accepted`
      vigent es pot mostrar.
    * `conflict`: alguna guarda falla (o l'evidencia no es concloent); es desa amb el
      detall i queda pendent de revisio. Mai no es mostra com a vigent.
    * `no_value`: la font publica l'import a 0 o buit (NULL). Es desa tal com es
      publica, amb la procedencia, pero mai no es un import economic valid ni entra a
      cap calcul.
    * `superseded`: una publicacio posterior de la mateixa font la substitueix, i
      l'identificador de publicacio, la fase i les dates ho demostren alhora.
    """

    ACCEPTED = "accepted"
    CONFLICT = "conflict"
    NO_VALUE = "no_value"
    SUPERSEDED = "superseded"
