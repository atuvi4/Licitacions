"""Traduccio dels codis de PLACSP al model canonic.

Les taules d'aquest modul NO estan inventades: surten de les llistes oficials
en format genericode (.gc) que el propi XML referencia a l'atribut `listURI` de
cada element, descarregades de contrataciondelestado.es:

  * SyndicationContractFolderStatusCode-2.04.gc  -> estat de l'expedient
  * ContractCode-2.08.gc                         -> tipus de contracte
  * SyndicationTenderingProcessCode-2.07.gc      -> tipus de procediment

Regla: si arriba un codi que no coneixem, NO s'inventa res. Es guarda el codi
original a `source_status_code` i el camp canonic queda a `UNKNOWN`/`OTHER`.
"""

from __future__ import annotations

from app.models.enums import ContractType, ProcedureType, TenderDocumentType, TenderStatus

# --- Estat de l'expedient (SyndicationContractFolderStatusCode-2.04) --------
# PRE=Anuncio Previo · PUB=EN PLAZO · EV=PENDIENTE DE ADJUDICACION
# ADJ=Adjudicada · RES=Resuelta · ANUL=Anulada
TENDER_STATUS: dict[str, TenderStatus] = {
    "PRE": TenderStatus.PRE_ANNOUNCEMENT,
    "PUB": TenderStatus.OPEN,
    "EV": TenderStatus.PENDING_AWARD,
    "ADJ": TenderStatus.AWARDED,
    "RES": TenderStatus.RESOLVED,
    "ANUL": TenderStatus.CANCELLED,
}

# --- Tipus de contracte (ContractCode-2.08) --------------------------------
CONTRACT_TYPE: dict[str, ContractType] = {
    "1": ContractType.GOODS,                        # Suministros
    "2": ContractType.SERVICES,                     # Servicios
    "3": ContractType.WORKS,                        # Obras
    "7": ContractType.SPECIAL_ADMINISTRATIVE,       # Administrativo especial
    "8": ContractType.PRIVATE,                      # Privado
    "21": ContractType.PUBLIC_SERVICES_MANAGEMENT,  # Gestion de Servicios Publicos
    "22": ContractType.SERVICES_CONCESSION,         # Concesion de Servicios
    "31": ContractType.PUBLIC_WORKS_CONCESSION,     # Concesion de Obras Publicas
    "32": ContractType.WORKS_CONCESSION,            # Concesion de Obras
    "40": ContractType.PUBLIC_PRIVATE_PARTNERSHIP,  # Colaboracion publico-privada
    "50": ContractType.PATRIMONIAL,                 # Patrimonial
}

# --- Tipus de procediment (SyndicationTenderingProcessCode-2.07) -----------
PROCEDURE_TYPE: dict[str, ProcedureType] = {
    "1": ProcedureType.OPEN,                              # Abierto
    "2": ProcedureType.RESTRICTED,                        # Restringido
    "3": ProcedureType.NEGOTIATED_WITHOUT_PUBLICITY,      # Negociado sin publicidad
    "4": ProcedureType.NEGOTIATED_WITH_PUBLICITY,         # Negociado con publicidad
    "5": ProcedureType.COMPETITIVE_DIALOGUE,              # Dialogo competitivo
    "6": ProcedureType.MINOR_CONTRACT,                    # Contrato menor
    "7": ProcedureType.FRAMEWORK_DERIVED,                 # Derivado de acuerdo marco
    "8": ProcedureType.DESIGN_CONTEST,                    # Concurso de proyectos
    "9": ProcedureType.OPEN_SIMPLIFIED,                   # Abierto simplificado
    "10": ProcedureType.INNOVATION_PARTNERSHIP,           # Asociacion para la innovacion
    "11": ProcedureType.INNOVATION_PARTNERSHIP_DERIVED,
    "12": ProcedureType.DYNAMIC_PURCHASING_SYSTEM,        # Sistema dinamico de adquisicion
    "13": ProcedureType.TENDER_WITH_NEGOTIATION,          # Licitacion con negociacion
    "100": ProcedureType.INTERNAL_RULES,                  # Normas internas
    "999": ProcedureType.OTHER,                           # Otros
}

# --- Tipus de document (TenderingDocumentTypeCode / element d'origen) ------
# El feed dona el tipus de dues maneres: pel nom de l'element CODICE que
# conte la referencia i, quan hi es, per un codi de document.
DOCUMENT_TYPE_BY_ELEMENT: dict[str, TenderDocumentType] = {
    "LegalDocumentReference": TenderDocumentType.ADMINISTRATIVE_TERMS,
    "TechnicalDocumentReference": TenderDocumentType.TECHNICAL_TERMS,
    "AdditionalDocumentReference": TenderDocumentType.ADDITIONAL,
}

DOCUMENT_TYPE_BY_CODE: dict[str, TenderDocumentType] = {
    "PLIEGO": TenderDocumentType.ADMINISTRATIVE_TERMS,
    "PCAP": TenderDocumentType.ADMINISTRATIVE_TERMS,
    "PPT": TenderDocumentType.TECHNICAL_TERMS,
    "DOC_CN": TenderDocumentType.NOTICE,
    "DOC_CD": TenderDocumentType.NOTICE,
    "ACTA_ADJ": TenderDocumentType.AWARD,
    "DOC_CAN_ADJ": TenderDocumentType.AWARD,
    "ACTA_RES": TenderDocumentType.AWARD,
}


def map_status(code: str | None) -> TenderStatus:
    return TENDER_STATUS.get((code or "").strip().upper(), TenderStatus.UNKNOWN)


def map_contract_type(code: str | None) -> ContractType:
    return CONTRACT_TYPE.get((code or "").strip(), ContractType.UNKNOWN)


def map_procedure_type(code: str | None) -> ProcedureType:
    return PROCEDURE_TYPE.get((code or "").strip(), ProcedureType.UNKNOWN)


def map_document_type(element_name: str | None, code: str | None) -> TenderDocumentType:
    if code and code.strip().upper() in DOCUMENT_TYPE_BY_CODE:
        return DOCUMENT_TYPE_BY_CODE[code.strip().upper()]
    if element_name and element_name in DOCUMENT_TYPE_BY_ELEMENT:
        return DOCUMENT_TYPE_BY_ELEMENT[element_name]
    return TenderDocumentType.OTHER
