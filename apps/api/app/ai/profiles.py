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

from app.ai.contract_v3 import parse_chunk_v3_result
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
from app.ai.prompts.requirements_v3_2 import (
    EXTRACTOR_VERSION_V3_2,
    PROMPT_VERSION_V3_2,
    SYSTEM_PROMPT_V3_2,
    build_user_message_v3_2,
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
from app.ai.schemas_v3 import (
    SCHEMA_VERSION_V3,
    ChunkExtractionOutV3,
    chunk_output_json_schema_v3,
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

    #: Cert si el perfil segueix l'Extraction Contract v3 (claims, suport per
    #: cita, candidats a criteri). La canonada hi entra per una branca propia i
    #: la de v1/v2 no es toca. Veure `app.ai.contract_v3`.
    claims_contract: bool = False
    #: Com es valida la resposta. `None` = `output_model.model_validate`, com
    #: sempre. La v3 valida element a element (`parse_chunk_v3_result`).
    parser: Callable[[Any], Any] | None = None

    def parse(self, payload: Any) -> Any:
        if self.parser is not None:
            return self.parser(payload)
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

#: **Extraction Contract v3. Intern: no es pot demanar per nom.** Existeix
#: perque la canonada v3 es pugui provar de cap a cap amb el proveidor fals.
#: No es a `PROFILES` a proposit: `PROFILES` es el registre que resolen els jobs,
#: els pressupostos i el benchmark a partir d'un nom que arriba de fora. Mentre
#: la v3 no s'hagi mesurat, nomes s'executa si el codi la passa explicitament
#: (`analyze_tender_requirements(..., profile=V3)`).
#:
#: Des del canari pagat de Palamos, el contracte es el **v3.1** (unitats fisiques,
#: claims rebutjats un a un, ancores aplanades). Les tres versions pugen juntes:
#: un run v3 (`bed24c45`, `4df81a00`) NO es pot reprendre amb aquest perfil, i
#: `plan_resume` ho refusa (`profile_mismatch`). Aquells runs queden com a
#: evidencia; el prompt v3 es conserva a `prompts/requirements_v3.py`.
#:
#: Des del canari v3.1 (`4087ac26`), el contracte es el **v3.2** (sobre robust,
#: metadades auxiliars element a element, classe d'adjudicacio per evidencia).
#: Tampoc no es repren cap run v3 ni v3.1; el prompt v3.1 es conserva a
#: `prompts/requirements_v3_1.py`.
V3 = ExtractorProfile(
    name="v3",
    extractor_version=EXTRACTOR_VERSION_V3_2,
    prompt_version=PROMPT_VERSION_V3_2,
    schema_version=SCHEMA_VERSION_V3,
    system_prompt=SYSTEM_PROMPT_V3_2,
    build_user_message=build_user_message_v3_2,
    json_schema=chunk_output_json_schema_v3,
    output_model=ChunkExtractionOutV3,
    structured_values=True,
    claims_contract=True,
    parser=parse_chunk_v3_result,
)

#: Els perfils que es poden demanar per NOM (jobs, pressupostos, benchmark).
PROFILES: dict[str, ExtractorProfile] = {profile.name: profile for profile in (V1, V2)}
#: Perfils que existeixen pero nomes s'executen si el codi els passa directament.
INTERNAL_PROFILES: dict[str, ExtractorProfile] = {V3.name: V3}

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
    for profile in (*PROFILES.values(), *INTERNAL_PROFILES.values()):
        if profile.extractor_version == extractor_version:
            return profile
    return V1
