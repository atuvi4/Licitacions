"""Prompt d'extraccio de requisits del perfil `product-v2`.

Es el prompt V1 (que NO es toca: `requirements_v1.py`) amb tres blocs nous, fruit dels
canaris reals de 2026-10-11:

1. **Cita literal (verbatim).** Dos requisits ben detectats es van perdre perque la
   cita no era literal i el backend (correctament) la va rebutjar. Ara el prompt diu
   que la cita es una copia caracter a caracter i que el que es pot redactar es el
   titol i la descripcio.
2. **Atomicitat.** Obligacions independents de la mateixa clausula, requisits
   separats amb la seva cita.
3. **Incerteses materials.** «unknown != no» i «unknown != omit»: una clausula
   material que no permet concloure res va a `uncertainties` (persistides a 0062),
   mai a requisit o criteri cert, i mai desapareix en silenci.

v2.1: un bloc de VOCABULARIS TANCATS (les categories de requisit, generades de l'enum, i la
separacio explicita amb els ambits de les incerteses).

Dues linies de V1 contradeien aquests blocs ("si no pots citar, no ho incloguis" i
"confianca baixa -> tambe a incerteses"): es substitueixen NOMES a la copia composta.

**No s'edita en silenci**: un canvi de significat demana una `PROMPT_VERSION` nova
(es part de la identitat del run, de la reutilitzacio, de la represa i de l'historial
de cost).
"""

from __future__ import annotations

from app.ai.prompts.requirements_v1 import SYSTEM_PROMPT as _V1_SYSTEM_PROMPT
from app.ai.prompts.requirements_v1 import build_user_message as _v1_build_user_message
from app.models.enums import RequirementCategory, UncertaintyScope

#: Identifica aquest text. Diferent de qualsevol versio v1/v2/v3. v2.1: vocabularis tancats.
PROMPT_VERSION_PRODUCT_V2 = "requirements-product-v2.1"
#: Identifica tota la canonada d'extraccio del perfil (amb la persistencia de 0062).
EXTRACTOR_VERSION_PRODUCT_V2 = "tender-requirements-product-v2"

_V1_TAIL = "MATERIAL NO CONFIABLE"

_PRODUCT_V2_RULES = """\
CITA LITERAL (VERBATIM)

La `quote` es una COPIA caracter a caracter d'un fragment continu de la PAGE que
declares a `page_number`, del text tal com el reps:
  - no tradueixis;
  - no posis ni treguis accents, ni la c trencada, ni la ela geminada;
  - no corregeixis l'ortografia, encara que el text tingui errors;
  - no canviis majuscules ni minuscules;
  - no canviis la puntuacio ni els espais interns;
  - no resumeixis, no parafrasegis i no "milloris" el catala o el castella.
El `title` i la `description` SI que els pots redactar amb les teves paraules. La
`quote`, mai. La pagina de la cita ha de ser la PAGE on apareix el fragment.
Si no pots copiar literalment el fragment que ho justifica: no l'inventis. Si es
material, registra-ho a `uncertainties`; si no ho es, omet l'element.

ATOMICITAT

Si una mateixa frase o clausula conte obligacions independents (que es poden complir,
acreditar o fer fallar la candidatura per separat: per exemple, un termini maxim de
resolucio d'avaries i una asseguranca de responsabilitat civil minima), retorna-les com
a requisits SEPARATS, cadascun amb la seva propia cita literal.

INCERTESES MATERIALS

"unknown" no vol dir "no", i "unknown" no vol dir "ometre". Si un fragment es material
per a la licitacio (scope: `admissibility`, `solvency`, `contract_obligation`, `scoring`,
`sizing`, `cost`, `submission_documentation`, `deadline` o `liability`) pero el text no
permet afirmar que s'exigeix (per exemple: "es determinara mes endavant", "si escau",
un valor que no es dona):
  - NO el converteixis en un requisit cert ni en un criteri cert;
  - NO el descartis en silenci;
  - registra'l a `uncertainties` amb `scope`, `extraction_id`, `page_number`, la
    `quote` literal si n'hi ha una i una `note` breu del que es indeterminat.
`scope` ha de ser EXACTAMENT un d'aquests nou valors; cap altre. La `note` es una
frase (com a molt 500 caracters), mai una copia del plec. `extraction_id` i
`page_number` son els del DOCUMENT i la PAGE on es la clausula, encara que no hi
posis cap `quote`.
No converteixis qualsevol frase vaga en una incertesa: nomes el que es material per
decidir o preparar la licitacio.

"""


#: Linies de V1 que contradiuen les regles de product-v2, i la seva substitucio. Nomes
#: canvia la copia composta: `requirements_v1.SYSTEM_PROMPT` queda intacte.
_V1_OVERRIDES: tuple[tuple[str, str], ...] = (
    (
        # V1 descartava en silenci; product-v2: si es material, va a incerteses.
        "Si no pots citar literalment on ho diu, NO incloguis l'element.",
        "Si no pots citar literalment on ho diu, NO l'incloguis com a requisit ni com a\n"
        "criteri. Si es material, registra-ho a `uncertainties` (vegeu INCERTESES\n"
        "MATERIALS); si no ho es, omet-lo.",
    ),
    (
        # V1 enviava tota confianca baixa a incerteses; ara nomes el que es material.
        "  - `low`: dubtes reals. Afegeix-ho tambe a `uncertainties`.",
        "  - `low`: dubtes reals sobre un requisit que el text SI exigeix. No el dupliquis\n"
        "    a `uncertainties`: aquella llista es nomes per al que el text no permet\n"
        "    afirmar que s'exigeix.",
    ),
)


def _vocabulary_rules() -> str:
    """v2.1 (post-mortem de l'Attempt 3): el model va posar un AMBIT d'incertesa
    (`submission_documentation`) com a `category` d'un requisit. El prompt V1 no enumera les
    categories (nomes l'esquema) i el de product-v2 si que enumera els ambits: ara totes dues
    llistes son explicites i separades. Generat de l'enum: no es pot desincronitzar."""
    category_values = [category.value for category in RequirementCategory]
    categories = ", ".join(f"`{value}`" for value in category_values)
    # Els ambits que NO son categories (`deadline` es valid a tots dos vocabularis).
    scope_only = ", ".join(
        f"`{scope.value}`" for scope in UncertaintyScope if scope.value not in category_values
    )
    return (
        "VOCABULARIS TANCATS\n\n"
        f"La `category` d'un requisit es EXACTAMENT un d'aquests valors: {categories}.\n"
        "Els ambits de les incerteses (`scope` de `uncertainties`) son un altre vocabulari.\n"
        f"Aquests valors son NOMES ambits i MAI una `category`: {scope_only}.\n"
        "La documentacio i la forma de presentacio de l'oferta (sobre, format electronic,\n"
        "declaracions) son `administrative_documentation`; els terminis d'un requisit son\n"
        "`deadline` (valid com a categoria i com a ambit). Si cap categoria no hi encaixa,\n"
        "`other`.\n\n"
    )


def _compose_system_prompt() -> str:
    """El prompt V1, amb les dues linies que contradiuen product-v2 substituides i els
    blocs nous abans del de material no confiable."""
    text = _V1_SYSTEM_PROMPT
    for old, new in _V1_OVERRIDES:
        if text.count(old) != 1:  # pragma: no cover - el V1 es immutable
            raise RuntimeError("El prompt V1 ha canviat: no es pot compondre product-v2.")
        text = text.replace(old, new)
    head, separator, tail = text.partition(_V1_TAIL)
    if not separator:  # pragma: no cover - el V1 es immutable
        raise RuntimeError("El prompt V1 ha canviat: no es pot compondre product-v2.")
    return head + _PRODUCT_V2_RULES + _vocabulary_rules() + separator + tail


SYSTEM_PROMPT_PRODUCT_V2 = _compose_system_prompt()


def build_user_message_product_v2(rendered_context: str) -> str:
    """El mateix embolcall que V1: el plec es material citable i no confiable."""
    return _v1_build_user_message(rendered_context)
