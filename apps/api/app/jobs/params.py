"""Parametres de cada tipus de feina i la seva identitat tecnica.

La `dedupe_key` decideix quan dues peticions poden compartir una execucio. Per a
una analisi hi entra tot el que en canvia el resultat o el cost: licitacio,
empremta dels plecs, perfil, proveidor, model, documents triats i si es `force`.
Si en canvia un sol element, son feines diferents.

La clau es un resum (SHA-256) i no la concatenacio: el nom d'un model pot ser
llarg, i una clau retallada podria fer coincidir dues feines que no ho son.
"""

from __future__ import annotations

import hashlib
import uuid

from pydantic import BaseModel, ConfigDict, Field


class _StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeParams(_StrictParams):
    profile: str
    force: bool = False
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class ProcessParams(_StrictParams):
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    force_download: bool = False
    force_reextract: bool = False


def _digest(*parts: object) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _ids(document_ids: list[uuid.UUID]) -> str:
    return ",".join(sorted(str(item) for item in document_ids)) or "*"


def analysis_dedupe_key(
    *,
    tender_id: uuid.UUID,
    fingerprint: str,
    profile: str,
    provider: str,
    model: str,
    document_ids: list[uuid.UUID],
    force: bool,
) -> str:
    """Identitat d'una analisi. `force` i normal son sempre claus diferents."""
    mode = "force" if force else "normal"
    digest = _digest(fingerprint, profile, provider, model, _ids(document_ids), mode)
    return f"analyze:{tender_id}:{mode}:{digest}"


def process_dedupe_key(*, tender_id: uuid.UUID, params: ProcessParams) -> str:
    digest = _digest(_ids(params.document_ids), params.force_download, params.force_reextract)
    return f"process:{tender_id}:{digest}"
