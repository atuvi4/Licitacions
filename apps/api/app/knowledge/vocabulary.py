"""Vocabularis globals del coneixement: el llenguatge comu entre el plec i l'empresa.

**Aquesta es la font de veritat.** Les taules `service_lines`,
`capability_definitions` i `certification_standards` en son una copia perque la
base pugui imposar les claus foranes; les omple la migracio `0045` i, el que
s'hi ha afegit despres, una migracio per ampliacio (`0046`: telefonia mobil).
`test_knowledge_vocabulary` comprova que, sumades, diuen el mateix que aquest
fitxer.

**Codis tancats.** No hi ha cap API que en creï. Un requisit del plec que no
encaixi amb cap capacitat quedara pendent de revisio i proposara un codi nou,
que una persona afegira aqui i en una migracio. Un model de llenguatge no pot
inventar codis.

Nomes hi ha el que necessita el pilot de l'Escala i que es generalitzable. No
hi ha res de cap empresa concreta.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import (
    CapabilityComparator,
    CapabilityValueType,
    EquipmentType,
    OperatingFactKey,
)


@dataclass(frozen=True, slots=True)
class ServiceLine:
    code: str
    label: str


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    code: str
    service_line_code: str
    label: str
    value_type: CapabilityValueType
    comparator: CapabilityComparator
    unit: str | None = None
    #: Nomes per a `ordinal`: de menor a major.
    ordinal_scale: tuple[str, ...] | None = None

    def rank(self, level: str) -> int:
        assert self.ordinal_scale is not None
        return self.ordinal_scale.index(level)


@dataclass(frozen=True, slots=True)
class CertificationStandard:
    code: str
    label: str
    #: Nivells de menor a major. `None` si la norma no en te.
    levels: tuple[str, ...] | None = None

    def rank(self, level: str) -> int:
        assert self.levels is not None
        return self.levels.index(level)


SERVICE_LINES: tuple[ServiceLine, ...] = (
    ServiceLine("voice_pbx", "Centraleta i veu IP"),
    ServiceLine("voice_trunk", "Troncals de veu i numeracio"),
    ServiceLine("access_ftth", "Accessos FTTH"),
    ServiceLine("dedicated_network", "Xarxa dedicada entre seus"),
    ServiceLine("radio_link", "Radioenllacos"),
    ServiceLine("network_switching", "Electronica de xarxa"),
    ServiceLine("fiber_deployment", "Desplegament i certificacio de fibra"),
    ServiceLine("civil_works", "Obra civil"),
    ServiceLine("support_ops", "Suport, operacio i nivells de servei"),
    # Afegida a la migracio 0046.
    ServiceLine("mobile_telephony", "Telefonia mobil"),
)

_B, _N, _O = CapabilityValueType.BOOLEAN, CapabilityValueType.NUMBER, CapabilityValueType.ORDINAL
_TRUE, _GTE, _LTE = CapabilityComparator.IS_TRUE, CapabilityComparator.GTE, CapabilityComparator.LTE

CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        "pbx.concurrent_calls", "voice_pbx", "Trucades simultanies", _N, _GTE, unit="canals"
    ),
    CapabilityDefinition("pbx.ivr_multilevel", "voice_pbx", "IVR multinivell", _B, _TRUE),
    CapabilityDefinition("pbx.queues", "voice_pbx", "Cues de trucades", _B, _TRUE),
    CapabilityDefinition("pbx.blf", "voice_pbx", "Estat de linia (BLF)", _B, _TRUE),
    CapabilityDefinition("pbx.call_park", "voice_pbx", "Aparcament de trucades", _B, _TRUE),
    CapabilityDefinition(
        "pbx.voicemail_email", "voice_pbx", "Bustia amb avis per correu", _B, _TRUE
    ),
    CapabilityDefinition("pbx.cdr_export", "voice_pbx", "CDR exportable", _B, _TRUE),
    CapabilityDefinition(
        "pbx.recording_policy", "voice_pbx", "Gravacio configurable per politica", _B, _TRUE
    ),
    CapabilityDefinition("pbx.tls_srtp", "voice_pbx", "Xifratge TLS i SRTP", _B, _TRUE),
    CapabilityDefinition("pbx.fraud_block", "voice_pbx", "Bloqueig contra frau", _B, _TRUE),
    CapabilityDefinition(
        "pbx.outbound_number_select",
        "voice_pbx",
        "Numeros de sortida seleccionables per usuari",
        _N,
        _GTE,
        unit="numeros",
    ),
    CapabilityDefinition(
        "pbx.extra_softphones_free",
        "voice_pbx",
        "Softphones addicionals sense cost",
        _N,
        _GTE,
        unit="llicencies",
    ),
    CapabilityDefinition(
        "pbx.queue_callback_keep_position",
        "voice_pbx",
        "Retorn de trucada conservant la posicio a la cua",
        _B,
        _TRUE,
    ),
    CapabilityDefinition("pbx.hot_desking", "voice_pbx", "Hot desking", _B, _TRUE),
    CapabilityDefinition(
        "switch.poe_standard",
        "network_switching",
        "Estandard PoE",
        _O,
        _GTE,
        ordinal_scale=("af", "at", "bt"),
    ),
    CapabilityDefinition(
        "switch.mlag", "network_switching", "Agregacio redundant (MLAG o equivalent)", _B, _TRUE
    ),
    CapabilityDefinition(
        "fiber.otdr_level",
        "fiber_deployment",
        "Nivell de certificacio de fibra (1 = OLTS, 2 = OTDR)",
        _O,
        _GTE,
        ordinal_scale=("1", "2"),
    ),
    CapabilityDefinition(
        "ops.sla_response_minutes", "support_ops", "Temps de resposta", _N, _LTE, unit="minuts"
    ),
    CapabilityDefinition(
        "ops.sla_restore_hours", "support_ops", "Temps de restabliment", _N, _LTE, unit="hores"
    ),
)

CERTIFICATION_STANDARDS: tuple[CertificationStandard, ...] = (
    CertificationStandard(
        "ENS", "Esquema Nacional de Seguretat", levels=("basic", "medium", "high")
    ),
    CertificationStandard("ISO_9001", "ISO 9001 (gestio de la qualitat)"),
    CertificationStandard("ISO_14001", "ISO 14001 (gestio ambiental)"),
    CertificationStandard("ISO_27001", "ISO/IEC 27001 (seguretat de la informacio)"),
)

SERVICE_LINE_CODES = frozenset(s.code for s in SERVICE_LINES)
CAPABILITIES_BY_CODE = {c.code: c for c in CAPABILITIES}
STANDARDS_BY_CODE = {s.code: s for s in CERTIFICATION_STANDARDS}


# --- Qualificacio i mitjans (0052) -------------------------------------------------
#
# Perfils professionals i tipus d'equip son codis tancats, com les linies de servei:
# una persona en pot cobrir diversos; un plec en demana per codi. Cap taula propia:
# viuen en llistes JSON validades aqui (`profile_codes`) o en un enum amb CHECK
# (`EquipmentType`). Res d'una empresa concreta.


@dataclass(frozen=True, slots=True)
class ProfessionalProfile:
    code: str
    label: str


PROFESSIONAL_PROFILES: tuple[ProfessionalProfile, ...] = (
    ProfessionalProfile("project_manager", "Cap de projecte"),
    ProfessionalProfile("network_security_specialist", "Especialista de xarxa i seguretat"),
    ProfessionalProfile("voip_specialist", "Especialista VoIP"),
    ProfessionalProfile("fiber_certification_specialist", "Responsable de fibra i certificacio"),
    ProfessionalProfile("network_engineer", "Enginyer de xarxa"),
    ProfessionalProfile("radio_link_specialist", "Especialista en radioenllacos"),
    ProfessionalProfile("field_technician", "Tecnic de camp"),
    ProfessionalProfile("service_desk_agent", "Agent de suport i atencio"),
    ProfessionalProfile("data_center_operator", "Operador de CPD"),
)
PROFESSIONAL_PROFILE_CODES = frozenset(p.code for p in PROFESSIONAL_PROFILES)
PROFILES_BY_CODE = {p.code: p for p in PROFESSIONAL_PROFILES}

EQUIPMENT_TYPE_LABELS: dict[str, str] = {
    EquipmentType.OTDR.value: "Reflectometre optic (OTDR)",
    EquipmentType.OLTS.value: "Equip de perdues optiques (OLTS)",
    EquipmentType.FUSION_SPLICER.value: "Fusionadora de fibra",
    EquipmentType.OPTICAL_POWER_METER.value: "Mesurador de potencia optica",
    EquipmentType.LIGHT_SOURCE.value: "Font de llum",
    EquipmentType.CABLE_CERTIFIER.value: "Certificador de cablejat",
    EquipmentType.SPECTRUM_ANALYZER.value: "Analitzador d'espectre",
    EquipmentType.OTHER.value: "Altres",
}
assert set(EQUIPMENT_TYPE_LABELS) == {t.value for t in EquipmentType}

#: Quin valor porta cada fet operatiu: un boolea (`value`) o, a mes, un nombre
#: (`value_number`). Un fet numeric amb `value = true` ha de dir quants.
OPERATING_FACT_VALUE_TYPES: dict[str, CapabilityValueType] = {
    OperatingFactKey.TELECOM_OPERATOR_CNMC.value: _B,
    OperatingFactKey.OWN_NUMBER_RANGE_NRN.value: _B,
    OperatingFactKey.DATACENTER_TIER3.value: _B,
    OperatingFactKey.OWN_FTTH_INFRASTRUCTURE.value: _B,
    OperatingFactKey.SUPPORT_24X7.value: _B,
    OperatingFactKey.IPV4_ASSIGNABLE_COUNT.value: _N,
}
assert set(OPERATING_FACT_VALUE_TYPES) == {k.value for k in OperatingFactKey}
NUMERIC_FACT_KEYS = frozenset(key for key, kind in OPERATING_FACT_VALUE_TYPES.items() if kind == _N)


def fact_value_type(key: str) -> CapabilityValueType:
    return OPERATING_FACT_VALUE_TYPES[str(key)]


def validate_fact_value(key: str, value: bool, value_number) -> None:
    """`ValueError` si el valor no te la forma que demana el fet.

    Un fet boolea no porta nombre. Un fet numeric afirmat (`value = true`) porta
    el nombre, mai negatiu; negat (`value = false`) no en porta cap.
    """
    kind = OPERATING_FACT_VALUE_TYPES.get(str(key))
    if kind is None:
        raise ValueError(f"Fet operatiu desconegut: {key}.")
    if kind == CapabilityValueType.BOOLEAN:
        if value_number is not None:
            raise ValueError(f"{key} es un fet de si o no: no porta nombre.")
        return
    if value and value_number is None:
        raise ValueError(f"{key} es un fet quantitatiu: cal `value_number`.")
    if not value and value_number is not None:
        raise ValueError(f"{key} negat no porta cap nombre.")
    if value_number is not None and value_number < 0:
        raise ValueError(f"{key} no pot ser negatiu.")


def validate_level(standard_code: str | None, level: str | None) -> None:
    """`ValueError` si el nivell no es de la norma. Un nivell sense norma no val."""
    if standard_code is None:
        if level is not None:
            raise ValueError("Un nivell de certificacio necessita una norma.")
        return
    standard = STANDARDS_BY_CODE.get(standard_code)
    if standard is None:
        raise ValueError(f"Norma desconeguda: {standard_code}.")
    if level is None:
        return
    if standard.levels is None or level not in standard.levels:
        raise ValueError(f"{level!r} no es un nivell de {standard_code}.")


def validate_capability_value(code: str, value_number, value_level: str | None) -> None:
    """`ValueError` si el valor no te la forma que demana la capacitat.

    Una booleana no porta valor; una numerica porta nombre; una ordinal porta un
    nivell de la seva escala. El valor pot faltar (`None`): aleshores la
    capacitat consta com a suportada pero sense dada comparable.
    """
    capability = CAPABILITIES_BY_CODE.get(code)
    if capability is None:
        raise ValueError(f"Capacitat desconeguda: {code}.")
    if capability.value_type == CapabilityValueType.BOOLEAN:
        if value_number is not None or value_level is not None:
            raise ValueError(f"{code} es una capacitat de si o no: no porta valor.")
    elif capability.value_type == CapabilityValueType.NUMBER:
        if value_level is not None:
            raise ValueError(f"{code} porta un nombre, no un nivell.")
    else:
        if value_number is not None:
            raise ValueError(f"{code} porta un nivell de la seva escala, no un nombre.")
        if value_level is not None and value_level not in (capability.ordinal_scale or ()):
            raise ValueError(f"{value_level!r} no es de l'escala de {code}.")


async def seed_vocabulary(session) -> None:
    """Omple les taules globals amb aquest fitxer. Idempotent.

    La migracio ho fa a les bases reals; aixo serveix per a les bases que es creen
    amb `create_all` (la suite de tests).
    """
    from app.models.knowledge import (
        CapabilityDefinitionRow,
        CertificationStandardRow,
        ServiceLineRow,
    )

    for line in SERVICE_LINES:
        await session.merge(ServiceLineRow(code=line.code, label=line.label))
    await session.flush()
    for cap in CAPABILITIES:
        await session.merge(
            CapabilityDefinitionRow(
                code=cap.code,
                service_line_code=cap.service_line_code,
                label=cap.label,
                value_type=cap.value_type,
                comparator=cap.comparator,
                unit=cap.unit,
                ordinal_scale=list(cap.ordinal_scale) if cap.ordinal_scale else None,
            )
        )
    for standard in CERTIFICATION_STANDARDS:
        await session.merge(
            CertificationStandardRow(
                code=standard.code,
                label=standard.label,
                levels=list(standard.levels) if standard.levels else None,
            )
        )
    await session.flush()
