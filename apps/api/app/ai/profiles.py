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
from app.ai.prompts.requirements_product_v2 import (
    EXTRACTOR_VERSION_PRODUCT_V2,
    PROMPT_VERSION_PRODUCT_V2,
    SYSTEM_PROMPT_PRODUCT_V2,
    build_user_message_product_v2,
)
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
from app.ai.schemas_product_v2 import (
    SCHEMA_VERSION_PRODUCT_V2,
    TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT,
    TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_TOOL,
    ChunkExtractionOutProductV2,
    chunk_output_json_schema_product_v2,
    chunk_output_json_schema_product_v2_strict,
    parse_chunk_product_v2,
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

#: Codi de refus (porta) i d'error (canonada) quan un model de tool estricta es fa servir amb un
#: perfil sense esquema de transport estricte.
STRICT_SCHEMA_UNAVAILABLE = "strict_schema_unavailable"
#: Model `auto` sense `strict` amb un perfil sense revisio de transport de tool.
TOOL_TRANSPORT_UNAVAILABLE = "tool_transport_unavailable"
#: Clau de `provider_metadata` on el run desa el seu transport (`transport_identity`): la
#: represa nomes reprén un run del MATEIX transport.
TRANSPORT_METADATA_KEY = "transport"
#: Model `auto` (estricte o no) sense `thinking` `between_tools` (o alhora a `disabled`).
THINKING_UNCONFIGURED = "thinking_unconfigured"
THINKING_MISCONFIGURED = "thinking_misconfigured"


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
    #: Cert si el perfil retorna incerteses MATERIALS i la canonada les persisteix a
    #: `requirement_uncertainties` (0062). V1 i v2: fals (les descarten, com sempre).
    material_uncertainties: bool = False
    #: Com es valida la resposta. `None` = `output_model.model_validate`, com
    #: sempre. La v3 valida element a element (`parse_chunk_v3_result`).
    parser: Callable[[Any], Any] | None = None
    #: Esquema de TRANSPORT per a una tool `strict: true` (models de `LLM_STRICT_TOOL_MODELS`) i
    #: la seva versio. `None`: el perfil no es pot fer servir amb aquests models (la porta ho
    #: refusa abans de cap crida).
    strict_json_schema: Callable[[], dict[str, Any]] | None = None
    strict_transport_version: str | None = None
    #: Revisio del transport de tool NO estricta (models `auto` sense `strict`): la tool rep
    #: l'esquema canonic (`json_schema`) com a guia i el parser valida la resposta. `None`: el
    #: perfil no es pot fer servir amb aquests models.
    tool_transport_version: str | None = None

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

#: **Perfil de producte v2** (`product-v2`). NO es la `v2` de sobre (valors
#: estructurats, benchmark): es la V1 amb cites literals reforcades, atomicitat i
#: incerteses materials persistides (0062). Mateix esquema de requisits i criteris que
#: V1; versions propies d'extractor, prompt i esquema, de manera que la identitat, la
#: reutilitzacio, la represa, l'historial de cost i el benchmark son separats de V1.
#:
#: **Experimental i intern: no es pot demanar per nom.** Com la v3, viu a
#: `INTERNAL_PROFILES` i nomes s'executa si el codi el passa explicitament (canari,
#: operador des del codi). Cap tenant, quota, job, endpoint ni
#: `REQUIREMENTS_PRODUCT_PROFILE` no el resol. Promocionar-lo (passar-lo a `PROFILES`)
#: es una decisio explicita, en un commit a part, despres de l'Attempt 3.
PRODUCT_V2 = ExtractorProfile(
    name="product-v2",
    extractor_version=EXTRACTOR_VERSION_PRODUCT_V2,
    prompt_version=PROMPT_VERSION_PRODUCT_V2,
    schema_version=SCHEMA_VERSION_PRODUCT_V2,
    system_prompt=SYSTEM_PROMPT_PRODUCT_V2,
    build_user_message=build_user_message_product_v2,
    json_schema=chunk_output_json_schema_product_v2,
    output_model=ChunkExtractionOutProductV2,
    structured_values=False,
    material_uncertainties=True,
    # Les incerteses, una a una: una de mal formada no fa perdre els requisits.
    parser=parse_chunk_product_v2,
    strict_json_schema=chunk_output_json_schema_product_v2_strict,
    strict_transport_version=TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT,
    tool_transport_version=TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_TOOL,
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
INTERNAL_PROFILES: dict[str, ExtractorProfile] = {
    profile.name: profile for profile in (V3, PRODUCT_V2)
}
#: Perfils interns EXPERIMENTALS (subconjunt d'`INTERNAL_PROFILES`). A mes de no ser
#: demanables per nom: el seu run MAI es la base vigent de cap licitacio, i el seu
#: historial es separat (un run seu no supera, per a la represa, el d'un altre perfil,
#: ni a l'inreves). La v3 no hi es: conserva el comportament que ja tenia.
EXPERIMENTAL_PROFILES: dict[str, ExtractorProfile] = {PRODUCT_V2.name: PRODUCT_V2}

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


def requires_auto_tool(provider: Any) -> bool:
    """El proveidor real d'Anthropic amb un model de `LLM_AUTO_TOOL_CHOICE_MODELS`: no admet la
    tool forcada (`tool_choice` `auto`)."""
    # Dins de la funcio: `app.core.config` importa aquest modul en validar la configuracio.
    from app.core.config import settings

    return (
        getattr(provider, "name", None) == "anthropic"
        and getattr(provider, "model", None) in settings.LLM_AUTO_TOOL_CHOICE_MODELS
    )


def requires_strict_tool(provider: Any) -> bool:
    """Un model `auto` que, a mes, envia la tool amb `strict: true` (`LLM_STRICT_TOOL_MODELS`) i
    ha de rebre l'esquema de transport estricte."""
    from app.core.config import settings

    return requires_auto_tool(provider) and provider.model in settings.LLM_STRICT_TOOL_MODELS


def transport_json_schema(profile: ExtractorProfile, provider: Any) -> dict[str, Any]:
    """L'esquema que viatja a la tool: el canonic del perfil (tool forcada o `auto` sense
    `strict`, on nomes guia el model) o, per a una tool estricta, el seu esquema de transport.
    Sense el transport que cal: error FATAL abans de cap peticio (la porta ja ho refusa abans de
    reservar el run)."""
    from app.ai.provider import ProviderError

    if requires_strict_tool(provider):
        if profile.strict_json_schema is None:
            raise ProviderError(
                f"El perfil {profile.name} no te esquema de transport estricte: no s'envia.",
                STRICT_SCHEMA_UNAVAILABLE,
                fatal=True,
            )
        return profile.strict_json_schema()
    if requires_auto_tool(provider) and profile.tool_transport_version is None:
        raise ProviderError(
            f"El perfil {profile.name} no te transport de tool: no s'envia.",
            TOOL_TRANSPORT_UNAVAILABLE,
            fatal=True,
        )
    return profile.json_schema()


def auto_tool_refusal(profile: ExtractorProfile, provider: Any) -> str | None:
    """Per a un model `auto`, el codi que la porta refusa abans de reservar el run (o `None`):
    perfil sense el transport que cal (estricte o de tool), o `thinking` no configurat (sense
    `between_tools`, el model pensaria abans de la tool dins del mateix `max_tokens`) o
    contradictori."""
    from app.core.config import settings

    if not requires_auto_tool(provider):
        return None
    if requires_strict_tool(provider):
        if profile.strict_json_schema is None:
            return STRICT_SCHEMA_UNAVAILABLE
    elif profile.tool_transport_version is None:
        return TOOL_TRANSPORT_UNAVAILABLE
    model = provider.model
    if model not in settings.LLM_THINKING_BETWEEN_TOOLS_MODELS:
        return THINKING_UNCONFIGURED
    if model in settings.LLM_THINKING_DISABLED_MODELS:
        return THINKING_MISCONFIGURED
    return None


def transport_identity(profile: ExtractorProfile, provider: Any) -> dict[str, Any] | None:
    """Que identifica el transport d'un model `auto` (entra a la identitat de l'analisi i es
    desa al run): `tool_choice` + instruccio de sortida, si la tool es estricta, la revisio de
    l'esquema que viatja (estricte v1, estricte v2 o de tool) i `thinking`. `None` per a la
    resta: la seva identitat no canvia."""
    if not requires_auto_tool(provider):
        return None
    from app.ai.providers.anthropic import AUTO_TOOL_TRANSPORT_VERSION
    from app.core.config import settings

    strict = requires_strict_tool(provider)
    return {
        "transport": AUTO_TOOL_TRANSPORT_VERSION,
        "strict": strict,
        "schema": profile.strict_transport_version if strict else profile.tool_transport_version,
        "thinking": (
            "between_tools"
            if provider.model in settings.LLM_THINKING_BETWEEN_TOOLS_MODELS
            else "default"
        ),
    }


#: Regla que es registra a la cobertura del chunk (`normalised`) per cada llista OPCIONAL de
#: l'esquema enviat que el model no ha retornat: `absent_optional_list:<nom>`. Absent no es
#: buida: una llista d'incerteses absent no diu que el model hagi comprovat que no n'hi ha.
ABSENT_OPTIONAL_LIST = "absent_optional_list"


@dataclass(frozen=True, slots=True)
class EnvelopeCheck:
    """Resultat de validar l'envoltori d'un model `auto`."""

    #: Per que es fatal, o `None` si l'envoltori es valid.
    problem: str | None
    #: Llistes opcionals de l'esquema enviat que la resposta no porta (ordenades).
    absent_optional: tuple[str, ...] = ()

    @property
    def rules(self) -> list[str]:
        return [f"{ABSENT_OPTIONAL_LIST}:{name}" for name in self.absent_optional]


def auto_tool_envelope_check(
    data: Any, profile: ExtractorProfile, *, schema: dict[str, Any] | None = None
) -> EnvelopeCheck:
    """Per a un model `auto`, l'envoltori contra l'esquema QUE S'HA ENVIAT (`schema`; per
    defecte el canonic del perfil, que es el que viatja sense tool estricta).

    * Nomes son obligatories les claus del `required` de l'arrel d'aquest esquema: el validador
      no exigeix res que la tool no hagi demanat (primer run real de L'Escala, 2026-10-14).
    * Com a minim una llista de l'esquema: `{}` (o nomes claus inventades) no pot passar per un
      chunk sense res. Fatal.
    * Cap clau fora de les `properties`, i les presents han de ser llistes. Fatal.
    * Les llistes opcionals absents s'accepten i es tornen a `absent_optional`: el parser hi
      aplica el seu `default_factory` (cap dada inventada) i la canonada registra l'absencia.
    """
    if not isinstance(data, dict):
        return EnvelopeCheck("no es un objecte")
    schema = profile.json_schema() if schema is None else schema
    known = set(schema.get("properties") or ())
    required = set(schema.get("required") or ())
    present = set(data)
    missing_required = sorted(required - present)
    if missing_required:
        return EnvelopeCheck(f"falten {', '.join(missing_required)}")
    if not present & known:
        return EnvelopeCheck(f"falten totes les llistes ({', '.join(sorted(known))})")
    extra = sorted(present - known)
    if extra:
        # El missatge arriba a `run.error_message` (API): una clau inventada pel model pot ser
        # text del plec, aixi que nomes es mostra si te forma d'identificador.
        from app.ai.output_normalization import _safe_key

        shown = [_safe_key(key) for key in extra[:5]]
        more = f" (+{len(extra) - 5})" if len(extra) > 5 else ""
        return EnvelopeCheck(f"claus inesperades {', '.join(shown)}{more}")
    not_lists = sorted(key for key in present & known if not isinstance(data[key], list))
    if not_lists:
        return EnvelopeCheck(f"no son llistes: {', '.join(not_lists)}")
    return EnvelopeCheck(None, tuple(sorted(known - present)))


def auto_tool_envelope_problem(
    data: Any, profile: ExtractorProfile, *, schema: dict[str, Any] | None = None
) -> str | None:
    """`auto_tool_envelope_check(...).problem`: `None` si l'envoltori es valid."""
    return auto_tool_envelope_check(data, profile, schema=schema).problem
