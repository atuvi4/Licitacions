"""Registre de fonts d'ingesta i alta idempotent de la fila Source."""

from __future__ import annotations

import pytest

from app.ingestion.placsp.service import PlacspIngestionService
from app.ingestion.sources import SOURCES


def test_registre_de_fonts():
    agregades = SOURCES["placsp_agregadas"]
    assert agregades.feed_url.endswith("sindicacion_1044/PlataformasAgregadasSinMenores.atom")
    assert agregades.name == "PLACSP · plataformes agregades"
    assert SOURCES["placsp"].feed_url.endswith("licitacionesPerfilesContratanteCompleto3.atom")


async def test_get_or_create_source_per_codi_es_idempotent(db, storage):
    s1 = await PlacspIngestionService(db, storage).get_or_create_source("placsp_agregadas")
    s2 = await PlacspIngestionService(db, storage).get_or_create_source("placsp_agregadas")
    assert s1.id == s2.id
    assert s1.base_url == SOURCES["placsp_agregadas"].feed_url
    assert s1.code == "placsp_agregadas"


async def test_codi_per_defecte_continua_sent_placsp(db, storage):
    source = await PlacspIngestionService(db, storage).get_or_create_source()
    assert source.code == "PLACSP"


async def test_codi_desconegut_falla(db, storage):
    with pytest.raises(KeyError):
        await PlacspIngestionService(db, storage).get_or_create_source("inexistent")


async def test_omple_base_url_nul_en_fila_existent(db, storage):
    from app.models.enums import SourceType
    from app.models.sources import Source

    definition = SOURCES["placsp_agregadas"]
    db.add(
        Source(
            code=definition.code,
            name=definition.name,
            source_type=SourceType.SYNDICATION_ATOM,
            base_url=None,
            enabled=True,
        )
    )
    await db.flush()
    service = PlacspIngestionService(db, storage)
    source = await service.get_or_create_source("placsp_agregadas")
    assert source.base_url == definition.feed_url
    again = await service.get_or_create_source("placsp_agregadas")
    assert again.id == source.id and again.base_url == definition.feed_url


async def test_304_a_la_primera_pagina_no_processa_res(db, storage, monkeypatch):
    import httpx

    from app.ingestion.placsp import service as service_module
    from app.ingestion.placsp.client import PlacspClient
    from app.models.enums import IngestionStatus

    transport = httpx.MockTransport(lambda request: httpx.Response(304))
    monkeypatch.setattr(
        service_module,
        "PlacspClient",
        lambda: PlacspClient(client=httpx.AsyncClient(transport=transport), pause_seconds=0),
    )
    run = await PlacspIngestionService(db, storage).run(max_files=3)
    assert run.status == IngestionStatus.SUCCESS
    assert run.pages_processed == 1
    assert run.records_fetched == 0 and run.records_failed == 0
