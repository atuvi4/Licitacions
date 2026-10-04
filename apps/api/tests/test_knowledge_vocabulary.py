"""Vocabularis globals del coneixement: tancats, genèrics i iguals a la migracio."""

from __future__ import annotations

import importlib.util
import unicodedata
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.knowledge.vocabulary import (
    CAPABILITIES,
    CERTIFICATION_STANDARDS,
    SERVICE_LINE_CODES,
    SERVICE_LINES,
    seed_vocabulary,
    validate_level,
)
from app.models.enums import CapabilityComparator, CapabilityValueType
from app.models.knowledge import CapabilityDefinitionRow, CertificationStandardRow, ServiceLineRow

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "0045_company_knowledge.py"
#: Les migracions posteriors que amplien el vocabulari, en ordre.
LATER_MIGRATIONS = (API_ROOT / "alembic" / "versions" / "0046_mobile_telephony_line.py",)


def _migration(path: Path = MIGRATION):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_the_pilot_needs_are_covered():
    assert {
        "voice_pbx",
        "voice_trunk",
        "access_ftth",
        "dedicated_network",
        "radio_link",
        "network_switching",
        "fiber_deployment",
        "civil_works",
        "support_ops",
        "mobile_telephony",
    } <= SERVICE_LINE_CODES
    assert {c.code for c in CAPABILITIES} == {
        "pbx.concurrent_calls",
        "pbx.ivr_multilevel",
        "pbx.queues",
        "pbx.blf",
        "pbx.call_park",
        "pbx.voicemail_email",
        "pbx.cdr_export",
        "pbx.recording_policy",
        "pbx.tls_srtp",
        "pbx.fraud_block",
        "pbx.outbound_number_select",
        "pbx.extra_softphones_free",
        "pbx.queue_callback_keep_position",
        "pbx.hot_desking",
        "switch.poe_standard",
        "switch.mlag",
        "fiber.otdr_level",
        "ops.sla_response_minutes",
        "ops.sla_restore_hours",
    }
    assert {s.code: s.levels for s in CERTIFICATION_STANDARDS} == {
        "ENS": ("basic", "medium", "high"),
        "ISO_9001": None,
        "ISO_14001": None,
        "ISO_27001": None,
    }


def test_every_definition_is_coherent():
    for cap in CAPABILITIES:
        assert cap.service_line_code in SERVICE_LINE_CODES, cap.code
        if cap.value_type == CapabilityValueType.BOOLEAN:
            assert cap.comparator == CapabilityComparator.IS_TRUE, cap.code
        else:
            assert cap.comparator != CapabilityComparator.IS_TRUE, cap.code
        assert (cap.value_type == CapabilityValueType.ORDINAL) == (cap.ordinal_scale is not None)


def _without_accents(label: str) -> str:
    decomposed = unicodedata.normalize("NFD", label)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def test_the_migrations_seed_exactly_this_vocabulary():
    """El vocabulari es la 0045 mes el que hi han afegit les migracions posteriors.

    Codis, tipus, comparadors, unitats i escales: identics. Els retols nomes poden
    diferir en els accents (copy v3, F1): les migracions publicades no es reescriuen,
    i les pantalles llegeixen els retols d'aquest fitxer (`/knowledge/vocabulary`).
    """
    migration = _migration()
    seeded_lines = migration._SERVICE_LINES
    for path in LATER_MIGRATIONS:
        seeded_lines += _migration(path)._SERVICE_LINES
    assert tuple((code, _without_accents(label)) for code, label in seeded_lines) == tuple(
        (s.code, _without_accents(s.label)) for s in SERVICE_LINES
    )
    assert len(set(code for code, _ in seeded_lines)) == len(seeded_lines)

    def plain_capability(row):
        code, line, label, *rest = row
        return (code, line, _without_accents(label), *rest)

    assert tuple(plain_capability(row) for row in migration._CAPABILITIES) == tuple(
        (
            c.code,
            c.service_line_code,
            _without_accents(c.label),
            str(c.value_type),
            str(c.comparator),
            c.unit,
            list(c.ordinal_scale) if c.ordinal_scale else None,
        )
        for c in CAPABILITIES
    )
    assert tuple(
        (code, _without_accents(label), levels) for code, label, levels in migration._STANDARDS
    ) == tuple(
        (s.code, _without_accents(s.label), list(s.levels) if s.levels else None)
        for s in CERTIFICATION_STANDARDS
    )


def test_the_accent_tolerance_is_only_about_accents():
    """La comparacio amb les migracions no amaga cap canvi de text, nomes diacritics."""
    assert _without_accents("Troncals de veu i numeració") == "Troncals de veu i numeracio"
    assert _without_accents("Radioenllaços") == "Radioenllacos"
    assert _without_accents("Electrònica de xarxa") != _without_accents("Electrònica de xarxes")


@pytest.mark.parametrize(
    "path",
    [
        API_ROOT / "app" / "knowledge" / "vocabulary.py",
        MIGRATION,
        *LATER_MIGRATIONS,
        API_ROOT / "app" / "models" / "knowledge.py",
    ],
)
def test_no_company_is_hardcoded_in_the_global_layer(path):
    text = path.read_text(encoding="utf-8").lower()
    for name in (
        "indaleccius",
        "inatic",
        "templus",
        "3cx",
        "freepbx",
        "yealink",
        "sewan",
        "ion mobile",
        "cablem",
    ):
        assert name not in text, (path.name, name)


async def test_mobile_telephony_is_a_service_line_like_the_others(db):
    """Una linia de servei mes: al vocabulari, als codis valids i a la taula."""
    line = next(s for s in SERVICE_LINES if s.code == "mobile_telephony")
    assert line.label == "Telefonia mòbil"
    assert "mobile_telephony" in SERVICE_LINE_CODES
    await seed_vocabulary(db)
    await db.commit()
    rows = {r.code: r.label for r in await db.scalars(sa.select(ServiceLineRow))}
    assert rows == {s.code: s.label for s in SERVICE_LINES}
    assert rows["mobile_telephony"] == "Telefonia mòbil"


async def test_reseeding_updates_the_labels_with_merge(db):
    """Una base sembrada amb els retols antics (sense accents, com les migracions 0045 i
    0046) queda amb els retols d'aquest fitxer despres de `seed_vocabulary`: `merge` els
    actualitza per codi, sense duplicar cap fila."""
    migration = _migration()
    old_lines = migration._SERVICE_LINES
    for path in LATER_MIGRATIONS:
        old_lines += _migration(path)._SERVICE_LINES
    for code, label in old_lines:
        db.add(ServiceLineRow(code=code, label=label))
    await db.flush()
    for code, line, label, value_type, comparator, unit, scale in migration._CAPABILITIES:
        db.add(
            CapabilityDefinitionRow(
                code=code,
                service_line_code=line,
                label=label,
                value_type=value_type,
                comparator=comparator,
                unit=unit,
                ordinal_scale=scale,
            )
        )
    for code, label, levels in migration._STANDARDS:
        db.add(CertificationStandardRow(code=code, label=label, levels=levels))
    await db.commit()
    db.expire_all()
    stale = await db.get(ServiceLineRow, "voice_trunk")
    assert stale.label == "Troncals de veu i numeracio"

    await seed_vocabulary(db)
    await db.commit()
    db.expire_all()
    lines = {r.code: r.label for r in await db.scalars(sa.select(ServiceLineRow))}
    caps = {r.code: r.label for r in await db.scalars(sa.select(CapabilityDefinitionRow))}
    standards = {r.code: r.label for r in await db.scalars(sa.select(CertificationStandardRow))}
    assert lines == {s.code: s.label for s in SERVICE_LINES}
    assert caps == {c.code: c.label for c in CAPABILITIES}
    assert standards == {s.code: s.label for s in CERTIFICATION_STANDARDS}
    assert lines["voice_trunk"] == "Troncals de veu i numeració"
    assert caps["pbx.concurrent_calls"] == "Trucades simultànies"
    assert standards["ISO_9001"] == "ISO 9001 (gestió de la qualitat)"


def test_no_route_can_create_vocabulary():
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    for fragment in (
        "service-lines",
        "capability-definitions",
        "certification-standards",
        "service_lines",
        "capability_definitions",
        "certification_standards",
    ):
        assert not [p for p in paths if fragment in p], fragment


def test_levels_belong_to_their_standard():
    validate_level("ENS", "medium")
    validate_level("ISO_9001", None)
    for standard, level in (
        ("ENS", "altissim"),
        ("ISO_9001", "high"),
        (None, "high"),
        ("ISO_45001", None),
    ):
        with pytest.raises(ValueError):
            validate_level(standard, level)


async def test_seeding_is_idempotent(db):
    await seed_vocabulary(db)
    await seed_vocabulary(db)
    await db.commit()
    counts = [
        await db.scalar(sa.select(sa.func.count()).select_from(model))
        for model in (ServiceLineRow, CapabilityDefinitionRow, CertificationStandardRow)
    ]
    assert counts == [len(SERVICE_LINES), len(CAPABILITIES), len(CERTIFICATION_STANDARDS)]
    poe = await db.get(CapabilityDefinitionRow, "switch.poe_standard")
    assert poe.ordinal_scale == ["af", "at", "bt"]
