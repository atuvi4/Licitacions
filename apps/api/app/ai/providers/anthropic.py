"""Adaptador d'Anthropic. **L'unic fitxer del projecte que en coneix el SDK.**

Decisions que val la pena explicitar:

* El SDK s'importa dins del constructor. Aixi l'aplicacio arrenca encara que el
  paquet no estigui instal·lat o no hi hagi clau: nomes queda no disponible la
  funcio d'analisi, no tot el servei.
* La sortida estructurada es demana amb una **tool** amb `input_schema`. Amb la
  tool forcada (o `strict`) el proveidor en garanteix la forma; sense (`auto` no
  estricte, Sonnet 5.5) nomes la guia, i la resposta es valida igual pel parser
  del perfil. No es parseja mai text lliure (ni JSON dins del text).
* El model NO es una constant de domini: ve de `settings.LLM_MODEL`.
* No es loguega mai ni la clau ni el contingut dels plecs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.ai.provider import (
    MalformedToolArgumentsError,
    MultipleToolCallsError,
    NoToolCallError,
    OutputTruncatedError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ServedModelMismatchError,
    StructuredResult,
    UnexpectedToolCallError,
    Usage,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Nom de la tool per la qual es forca la sortida estructurada.
_TOOL_NAME = "record_extraction"

#: Errors HTTP que no depenen del chunk sino de la configuracio, del compte o de
#: la forma de la peticio (clau, credit, permisos, model inexistent). El SDK no
#: els reintenta mai, i continuar amb la resta de chunks nomes repetiria el mateix
#: error. 429, 408, 409 i 5xx NO hi son: son transitoris i els gestiona
#: `LLM_MAX_RETRIES` com sempre.
FATAL_HTTP_STATUSES = frozenset({400, 401, 403, 404})
#: Motius de parada que deixen la sortida incompleta: mai es fa servir, encara que hi hagi
#: una crida a la tool (un JSON tallat pot validar). `max_tokens`: el limit de sortida;
#: `model_context_window_exceeded`: la finestra de context del model.
TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
#: El model ha refusat respondre: cap contingut, encara que n'hi hagi, es fa servir.
REFUSAL_STOP_REASON = "refusal"

#: Per als models de `LLM_AUTO_TOOL_CHOICE_MODELS` (no admeten la tool forcada): la tool no es
#: pot imposar, aixi que el sistema diu inequivocament que la resposta es la crida a la tool.
AUTO_TOOL_INSTRUCTION = (
    "\n\nSORTIDA\n\n"
    f"Respon NOMES cridant la tool `{_TOOL_NAME}`, UNA sola vegada, amb tot el resultat del\n"
    "fragment com a input. No escriguis cap text fora de la tool: una resposta sense aquesta\n"
    "crida es descarta com a error. Si el fragment no conte res, crida igualment la tool amb\n"
    "les llistes buides.\n"
)
#: Identifica el transport `auto` (instruccio de sortida, `tool_choice: auto` i les fallades
#: FATALS: cap crida, diverses, una altra tool, arguments que no son objecte, motiu de parada,
#: model servit). Si la tool porta `strict` i quin esquema viatja, ho diu `transport_identity`.
#: Entra a la identitat de l'analisi dels models `auto`: canviar-lo es una analisi nova.
#: v3 (2026-10-14): l'envoltori nomes exigeix el `required` de l'esquema enviat; les llistes
#: opcionals absents s'accepten i es registren. Cap canvi en el que s'envia al proveidor.
AUTO_TOOL_TRANSPORT_VERSION = "anthropic-auto-tool-v3"
#: Limits de la tool estricta (documentacio d'Anthropic, structured outputs). Un esquema que
#: no hi cap rep un 400: es comprova abans, i no s'envia.
STRICT_MAX_OPTIONAL_PARAMETERS = 24
STRICT_MAX_UNION_PARAMETERS = 16
#: Paraules clau ADMESES per la tool estricta (llista blanca: qualsevol altra es un problema).
_STRICT_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf",
     "allOf", "$ref", "$defs", "definitions", "default", "description", "title", "format",
     "minItems", "pattern"}
)  # fmt: skip
_STRICT_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
#: Claus el valor de les quals es un diccionari de NOMS (no paraules clau) i claus amb valors
#: de dades (no esquemes): no s'hi busquen paraules clau.
_NAME_MAPPINGS = ("properties", "$defs", "definitions")
_DATA_VALUES = ("default", "enum", "const")


def _is_object(node: dict[str, Any]) -> bool:
    kind = node.get("type")
    return kind == "object" or (isinstance(kind, list) and "object" in kind) or "properties" in node


def _own_counts(node: dict[str, Any]) -> tuple[int, int]:
    """Parametres opcionals i amb unio de tipus d'UN objecte."""
    required = set(node.get("required") or ())
    optional = union = 0
    for name, prop in (node.get("properties") or {}).items():
        optional += name not in required
        union += isinstance(prop, dict) and ("anyOf" in prop or isinstance(prop.get("type"), list))
    return optional, union


def strict_schema_counts(schema: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """`(opcionals, amb unio)` comptats per definicio (cada objecte un cop) i per us (expandint
    els `$ref` locals). La comprovacio aplica el mes gran dels dos."""
    definitions = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}

    def per_definition(node: Any) -> tuple[int, int]:
        if isinstance(node, list):
            parts = [per_definition(v) for v in node]
            return sum(p[0] for p in parts), sum(p[1] for p in parts)
        if not isinstance(node, dict):
            return 0, 0
        own = _own_counts(node) if _is_object(node) else (0, 0)
        totals = [own]
        for key, value in node.items():
            if key in _DATA_VALUES:
                continue
            if key in _NAME_MAPPINGS and isinstance(value, dict):
                totals.extend(per_definition(sub) for sub in value.values())
            else:
                totals.append(per_definition(value))
        return sum(t[0] for t in totals), sum(t[1] for t in totals)

    def per_usage(node: Any, stack: frozenset[str]) -> tuple[int, int]:
        if isinstance(node, list):
            parts = [per_usage(v, stack) for v in node]
            return sum(p[0] for p in parts), sum(p[1] for p in parts)
        if not isinstance(node, dict):
            return 0, 0
        totals = [_own_counts(node) if _is_object(node) else (0, 0)]
        name = _local_ref(node.get("$ref"))
        if name is not None and name in definitions and name not in stack:
            totals.append(per_usage(definitions[name], stack | {name}))
        for key, value in node.items():
            if key in _DATA_VALUES or key in ("$defs", "definitions", "$ref"):
                continue
            if key == "properties" and isinstance(value, dict):
                totals.extend(per_usage(sub, stack) for sub in value.values())
            else:
                totals.append(per_usage(value, stack))
        return sum(t[0] for t in totals), sum(t[1] for t in totals)

    return {"per_definition": per_definition(schema), "per_usage": per_usage(schema, frozenset())}


def _local_ref(ref: Any) -> str | None:
    for prefix in ("#/$defs/", "#/definitions/"):
        if isinstance(ref, str) and ref.startswith(prefix):
            return ref[len(prefix) :]
    return None


def strict_schema_problems(schema: dict[str, Any]) -> list[str]:
    """Que impediria fer servir `schema` amb una tool `strict: true` (llista buida: res).

    Llista BLANCA de paraules clau; objectes (tambe `type: [..., "object"]`) sense
    `additionalProperties: false`; `minItems` diferent de 0 o 1; formats no admesos; `$ref` no
    locals o recursius; `allOf` amb `$ref`; enums amb valors compostos; i els limits de
    parametres opcionals i amb unio de tipus (el mes gran entre per definicio i per us)."""
    problems: list[str] = []
    definitions = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}

    def recursive(name: str, seen: frozenset[str]) -> bool:
        def refs(node: Any) -> list[str]:
            if isinstance(node, list):
                return [r for v in node for r in refs(v)]
            if not isinstance(node, dict):
                return []
            found = [_local_ref(node.get("$ref"))] if _local_ref(node.get("$ref")) else []
            return found + [
                r for k, v in node.items() if k not in _DATA_VALUES and k != "$ref" for r in refs(v)
            ]

        return any(
            ref in seen or (ref in definitions and recursive(ref, seen | {ref}))
            for ref in refs(definitions.get(name))
        )

    def check(node: Any, path: str) -> None:
        if isinstance(node, list):
            for index, value in enumerate(node):
                check(value, f"{path}/{index}")
            return
        if not isinstance(node, dict):
            return
        problems.extend(f"{path}: {key}" for key in sorted(set(node) - _STRICT_KEYWORDS))
        if node.get("minItems", 0) not in (0, 1):
            problems.append(f"{path}: minItems")
        if "format" in node and node["format"] not in _STRICT_FORMATS:
            problems.append(f"{path}: format {node['format']}")
        if "$ref" in node and _local_ref(node["$ref"]) not in definitions:
            problems.append(f"{path}: $ref no local")
        if any(isinstance(b, dict) and "$ref" in b for b in node.get("allOf") or ()):
            problems.append(f"{path}: allOf amb $ref")
        if any(isinstance(v, dict | list) for v in node.get("enum") or ()):
            problems.append(f"{path}: enum compost")
        if _is_object(node) and node.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties")
        for key, value in node.items():
            if key in _DATA_VALUES:
                continue
            if key in _NAME_MAPPINGS and isinstance(value, dict):
                for name, sub in value.items():
                    check(sub, f"{path}/{key}/{name}")
            else:
                check(value, f"{path}/{key}")

    check(schema, "#")
    problems.extend(
        f"#/$defs/{name}: esquema recursiu"
        for name in sorted(definitions)
        if recursive(name, frozenset({name}))
    )
    counts = strict_schema_counts(schema)
    optional = max(counts["per_definition"][0], counts["per_usage"][0])
    union = max(counts["per_definition"][1], counts["per_usage"][1])
    if optional > STRICT_MAX_OPTIONAL_PARAMETERS:
        problems.append(f"parametres opcionals: {optional}")
    if union > STRICT_MAX_UNION_PARAMETERS:
        problems.append(f"parametres amb unio de tipus: {union}")
    return problems


#: Llargada maxima del missatge del proveidor que es desa.
MAX_ERROR_MESSAGE_CHARS = 300
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def sanitise_provider_message(message: Any) -> str | None:
    """Una linia, sense cap clau i retallada. `None` si no n'hi ha."""
    if message is None:
        return None
    text = " ".join(str(message).split())
    text = _KEY_PATTERN.sub("[redacted]", text)
    if settings.ANTHROPIC_API_KEY:
        text = text.replace(settings.ANTHROPIC_API_KEY, "[redacted]")
    return text[:MAX_ERROR_MESSAGE_CHARS] or None


def status_error_details(exc: Any) -> dict[str, Any]:
    """Diagnostic d'un `APIStatusError`: estat, tipus, missatge i request_id."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    error_type = getattr(exc, "type", None) or error.get("type")
    request_id = getattr(exc, "request_id", None)
    if request_id is None and isinstance(body, dict):
        request_id = body.get("request_id")
    return {
        "status": getattr(exc, "status_code", None),
        "type": str(error_type) if error_type else None,
        "message": sanitise_provider_message(error.get("message")),
        "request_id": str(request_id) if request_id else None,
    }


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, max_retries: int | None = None) -> None:
        self._client: Any | None = None
        self._model = settings.LLM_MODEL
        #: Reintents automatics del SDK. `None` = `LLM_MAX_RETRIES` (produccio).
        #: El benchmark de pagament hi posa 0: el SDK reintenta sol en timeouts,
        #: 429 i 5xx, i cada intent que el proveidor arriba a processar es paga.
        self._max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        if not settings.ANTHROPIC_API_KEY:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not settings.ANTHROPIC_API_KEY:
            raise ProviderNotConfiguredError(
                "ANTHROPIC_API_KEY no esta configurada: l'analisi de plecs no esta disponible."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depen de l'entorn
            raise ProviderNotConfiguredError(
                "El paquet `anthropic` no esta instal·lat."
            ) from exc

        self._client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=self._max_retries,
        )
        return self._client

    def request_params(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        """El cos de `messages.create`. Cap parametre de mostreig (`temperature`, `top_p`,
        `top_k`): es fan servir els del model.

        * Per defecte: tool FORCADA (`tool_choice` de tipus `tool`). `thinking` NOMES per als
          models de `LLM_THINKING_DISABLED_MODELS`, i nomes per desactivar-lo.
        * Models de `LLM_AUTO_TOOL_CHOICE_MODELS` (no admeten la tool forcada): `tool_choice`
          `auto` i `AUTO_TOOL_INSTRUCTION` al final del sistema. La tool porta `strict: true`
          NOMES si el model tambe es a `LLM_STRICT_TOOL_MODELS`.
        * Models de `LLM_THINKING_BETWEEN_TOOLS_MODELS`: `thinking` `between_tools` (sense
          pensament abans de la tool). Mai alhora amb `disabled`: no s'envia res.
        """
        auto = self.auto_tool_choice
        disabled = self._model in settings.LLM_THINKING_DISABLED_MODELS
        between_tools = self._model in settings.LLM_THINKING_BETWEEN_TOOLS_MODELS
        if disabled and between_tools:
            raise ProviderError(
                f"{self._model} es alhora a LLM_THINKING_DISABLED_MODELS i a "
                "LLM_THINKING_BETWEEN_TOOLS_MODELS: no s'envia.",
                "provider_misconfigured",
                fatal=True,
            )
        tool: dict[str, Any] = {
            "name": _TOOL_NAME,
            "description": (
                "Registra els requisits i criteris trobats al fragment. "
                "Utilitza NOMES informacio explicita del text proporcionat."
            ),
            "input_schema": json_schema,
        }
        if self.strict_tool:
            tool["strict"] = True
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_output_tokens,
            "system": system.rstrip("\n") + AUTO_TOOL_INSTRUCTION if auto else system,
            "messages": [{"role": "user", "content": user_content}],
            "tools": [tool],
            # Per defecte, obliga el model a respondre per la tool: sense text lliure. Sense
            # tool forcada (`auto`), una resposta sense la tool es un error fatal.
            "tool_choice": {"type": "auto"} if auto else {"type": "tool", "name": _TOOL_NAME},
        }
        if disabled:
            params["thinking"] = {"type": "disabled"}
        elif between_tools:
            params["thinking"] = {"type": "between_tools"}
        return params

    @property
    def auto_tool_choice(self) -> bool:
        return self._model in settings.LLM_AUTO_TOOL_CHOICE_MODELS

    @property
    def strict_tool(self) -> bool:
        """`strict: true` nomes per als models `auto` que tambe son a `LLM_STRICT_TOOL_MODELS`."""
        return self.auto_tool_choice and self._model in settings.LLM_STRICT_TOOL_MODELS

    async def extract_structured(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredResult:
        client = self._ensure_client()
        import anthropic

        params = self.request_params(
            system=system,
            user_content=user_content,
            json_schema=json_schema,
            max_output_tokens=max_output_tokens,
        )
        if self.strict_tool:
            # Una tool estricta amb un esquema que no hi cap seria un 400: no s'envia.
            problems = strict_schema_problems(json_schema)
            if problems:
                raise ProviderError(
                    "L'esquema no es apte per a una tool estricta: no s'envia.",
                    "strict_schema_unsupported",
                    details={"problems": problems[:10]},
                    fatal=True,
                )
        try:
            response = await client.messages.create(**params)
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            details = status_error_details(exc)
            label = f" ({details['type']})" if details["type"] else ""
            raise ProviderError(
                f"HTTP {exc.status_code} del proveidor{label}.",
                "provider_error",
                details=details,
                fatal=exc.status_code in FATAL_HTTP_STATUSES,
            ) from exc
        except anthropic.APIError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        # L'us es llegeix abans de validar: una resposta inservible tambe s'ha pagat.
        raw_usage = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=getattr(raw_usage, "input_tokens", None),
            output_tokens=getattr(raw_usage, "output_tokens", None),
            request_id=getattr(response, "id", None),
            stop_reason=getattr(response, "stop_reason", None),
        )

        # Sense tool forcada (`auto`), qualsevol resposta que no sigui EXACTAMENT una crida a la
        # tool de sortida amb un objecte d'arguments es FATAL: es repetiria a cada chunk. Primer,
        # el model servit; despres el limit de sortida (o de context): una resposta tallada es
        # inservible encara que el tros que ha arribat sembli un JSON valid.
        auto = self.auto_tool_choice
        served = getattr(response, "model", None)
        if auto and served != self._model:
            raise ServedModelMismatchError(
                f"El proveidor ha servit {served!r} en lloc de {self._model!r}.", usage=usage
            )
        if usage.stop_reason in TRUNCATION_STOP_REASONS:
            error = OutputTruncatedError(
                f"La resposta s'ha tallat ({usage.stop_reason}; sortida maxima "
                f"{max_output_tokens} tokens).",
                usage=usage,
            )
            error.fatal = auto
            raise error
        if usage.stop_reason == REFUSAL_STOP_REASON:
            raise ProviderError(
                "El model ha refusat respondre (stop_reason=refusal).",
                "refusal",
                usage=usage,
                fatal=auto,
            )

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        calls = [block for block in tool_uses if block.name == _TOOL_NAME]
        if auto and len(calls) != len(tool_uses):
            error = UnexpectedToolCallError(
                "El proveidor ha cridat una tool que no es la de sortida.", usage=usage
            )
            error.fatal = True
            raise error
        if auto and len(calls) > 1:
            # El model pot repartir la resposta en diverses crides: no se'n tria cap (una sola
            # seria una sortida incompleta).
            error = MultipleToolCallsError(
                f"El proveidor ha cridat {len(calls)} vegades la tool de sortida.", usage=usage
            )
            error.fatal = True
            raise error
        if auto and calls and not isinstance(calls[0].input, dict):
            error = MalformedToolArgumentsError(
                "Els arguments de la tool de sortida no son un objecte.", usage=usage
            )
            error.fatal = True
            raise error
        if auto and calls and usage.stop_reason != "tool_use":
            # Una crida a la tool amb un motiu de parada que no es el de la tool (`end_turn`,
            # `pause_turn`, cap): la resposta no es la que s'espera; no es fa servir.
            raise ProviderError(
                f"Motiu de parada inesperat amb la tool: {usage.stop_reason}.",
                "unexpected_stop_reason",
                usage=usage,
                fatal=True,
            )
        payload: dict[str, Any] | None = dict(calls[0].input) if calls else None
        if payload is None:
            error = NoToolCallError(
                "El proveidor no ha retornat cap crida a la tool de sortida "
                f"(stop_reason={usage.stop_reason}).",
                usage=usage,
            )
            # Sense tool forcada, el model ha triat no cridar-la: es repetiria a cada chunk. Cap
            # text lliure com a alternativa, cap JSON llegit del text, cap reintent.
            error.fatal = self.auto_tool_choice
            raise error

        return StructuredResult(
            data=payload,
            usage=usage,
            model=getattr(response, "model", self._model),
        )
