"""Perfils d'extraccio: quina versio de prompt, esquema i contracte s'executa.

Existeix per no haver de duplicar la canonada d'extraccio a cada versio. El
chunking, la validacio d'evidencia, la resolucio de lots i la persistencia son
els mateixos; el que canvia es el contracte amb el model.

Cada perfil es una fotografia coherent: prompt, esquema de sortida i versio de
l'extractor van junts, i es desen junts a cada Run. Barrejar-los faria que dos
resultats incomparables semblessin comparables.

El per defecte segueix sent **V1**: la v2 esta implementada pero encara no
mesurada, i canviar el comportament de tota extraccio nova sense haver-la
comprovat seria promoure-la per accident. Qui vulgui la v2 la demana:
`analyze_tender_requirements(..., profile=V2)`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.ai.prompts.requirements_v1 import (
    EXTRACTOR_VERSION,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.ai.prompts.requirements_v2 import (
    EXTRACTOR_VERSION_V2,
    PROMPT_VERSION_V2,
    SYSTEM_PROMPT_V2,
    build_user_message_v2,
)
from app.ai.schemas import (
    SCHEMA_VERSION,
    ChunkExtractionOut,
    chunk_output_json_schema,
)
from app.ai.schemas_v2 import (
    SCHEMA_VERSION_V2,
    ChunkExtractionOutV2,
    chunk_output_json_schema_v2,
)


@dataclass(frozen=True, slots=True)
class ExtractorProfile:
    """Tot el que identifica una manera d'extreure, en un sol objecte."""

    name: str
    extractor_version: str
    prompt_version: str
    schema_version: str
    system_prompt: str
    build_user_message: Callable[[str], str]
    json_schema: Callable[[], dict[str, Any]]
    output_model: type
    #: Cert si el perfil produeix modalitat, alternatives i valor tipat. Ho
    #: consulten la deduplicacio i la persistencia; no hi ha cap `if version ==`
    #: escampat per la canonada.
    structured_values: bool

    def parse(self, payload: Any) -> Any:
        return self.output_model.model_validate(payload)


V1 = ExtractorProfile(
    name="v1",
    extractor_version=EXTRACTOR_VERSION,
    prompt_version=PROMPT_VERSION,
    schema_version=SCHEMA_VERSION,
    system_prompt=SYSTEM_PROMPT,
    build_user_message=build_user_message,
    json_schema=chunk_output_json_schema,
    output_model=ChunkExtractionOut,
    structured_values=False,
)

V2 = ExtractorProfile(
    name="v2",
    extractor_version=EXTRACTOR_VERSION_V2,
    prompt_version=PROMPT_VERSION_V2,
    schema_version=SCHEMA_VERSION_V2,
    system_prompt=SYSTEM_PROMPT_V2,
    build_user_message=build_user_message_v2,
    json_schema=chunk_output_json_schema_v2,
    output_model=ChunkExtractionOutV2,
    structured_values=True,
)

PROFILES: dict[str, ExtractorProfile] = {profile.name: profile for profile in (V1, V2)}

#: El perfil que s'executa si ningu no en demana cap altre.
#:
#: **Segueix sent V1, i no es un descuit.** La v2 esta implementada pero la seva
#: qualitat amb un model real encara no s'ha mesurat: promoure-la a per defecte
#: faria que qualsevol extraccio nova canviès de comportament sense que ningu ho
#: hagues demanat ni comprovat. Implementat i promogut a produccio son dues coses
#: diferents.
#:
#: Els runs de benchmark demanen la v2 EXPLICITAMENT
#: (`analyze_tender_requirements(..., profile=V2)`), i el perfil queda desat al
#: Run, de manera que sempre es pot dir amb quin contracte es va extreure.
#:
#: Quan 4D.2 mesuri la v2 i passi els criteris d'acceptacio, aixo passara a `V2`
#: en un commit a part, que es el que ha de portar la decisio.
DEFAULT_PROFILE = V1

#: El perfil que s'ha de demanar explicitament per als runs de benchmark de la
#: v2. Tenir-hi un nom evita que els guions l'escriguin a ma i es desincronitzin.
BENCHMARK_PROFILE = V2


def profile_for_run(extractor_version: str) -> ExtractorProfile:
    """El perfil amb que es va fer un run. Cau a v1 per als runs mes antics."""
    for profile in PROFILES.values():
        if profile.extractor_version == extractor_version:
            return profile
    return V1
