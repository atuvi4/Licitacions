"""La normalitzacio de titol que entra a les claus de deduplicacio.

Una sola funcio per a tothom qui calcula o compara una clau de requisit o de
criteri: l'extractor (`requirement_analysis.requirement_dedup_key` i
`award_dedup_key`), els requisits manuals (`manual_requirement.manual_dedup_key`)
i l'eina d'acceptacio (`app.evaluation.lescala_acceptance`). Una segona copia
inline divergiria sense que ningu ho notes.

Es un modul FULLA a proposit (cap import de `app`): `manual_requirement` no pot
importar `requirement_analysis` (cicle via `structure_annotations`) i la funcio
ha de ser la mateixa a tots dos costats.
"""

from __future__ import annotations


def normalized_title(text: str) -> str:
    """Espais col·lapsats i sense majuscules: `"  Preu  Oferta "` -> `"preu oferta"`."""
    return " ".join(text.split()).casefold()
