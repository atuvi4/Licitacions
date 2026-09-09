# Cobertura del gold — gold-v2

Derivat de `gold-v1-reviewed`, que queda immutable · esquema `gold-schema-v2` · 2026-09-09

## Recompte

| | |
| --- | ---: |
| Total d'anotacions | 17 |
| Requisits | 14 |
| Criteris d'adjudicacio | 3 |
| Casos | 11 |

| Estat | Anotacions |
| --- | ---: |
| verified | 15 |
| draft (propostes noves) | 0 |
| needs_review | 2 |
| rejected | 0 |

**Al denominador de les metriques: 14** de 17, perque 1 anotacio(ns) verificada(es) ha(n) estat substituida(es) per una versio afinada. Es conserven senceres i segueixen sent auditables.

| Origen | Total | Verificades |
| --- | ---: | ---: |
| Heretades de la v1 | 11 | 11 |
| Propostes noves | 6 | 4 |

Criteris d'adjudicacio verificats disponibles: **2**

## Categories cobertes

| Categoria | Verificades | Pendents |
| --- | ---: | ---: |
| business_classification | 0 | 1 |
| certification | 1 | 0 |
| economic_solvency | 3 | 0 |
| experience | 3 | 0 |
| insurance | 5 | 1 |

## Dimensions que el benchmark podra puntuar

Una dimensio nomes es mesurable si alguna anotacio **verificada** la fixa. Les pendents son el que hi haura si la revisio humana accepta les propostes; no son una mesura.

| Dimensio | Gold verificat | Pendent de revisio | Mesurable avui |
| --- | ---: | ---: | --- |
| obligation_modality | 2 | 1 | si |
| alternatives | n/d | 1 | n/d |
| aggregation | 1 | 0 | si |
| percentage_base | 1 | 0 | si |
| insurance_limits | 1 | 0 | si |
| equivalence | 1 | 0 | si |
| award_formula | 2 | 1 | si |

## Evidencia

23 de 24 cites localitzades al text real de la seva pagina.

1 anotacio(ns) amb la pagina no disponible en aquesta base de dades: no s'han pogut comprovar.

## Anotacions que n'afinen una altra

Descriuen **la mateixa exigencia** amb mes detall que una anotacio anterior. Les dues no poden comptar alhora: el gold demanaria dues coses on el plec en demana una. Substituir no esborra res —l'anotacio original conserva text, cita, revisor i data— nomes la treu del denominador.

| Anotacio | Estat | Afina | Com ha quedat l'afinada |
| --- | --- | --- | --- |
| TSA0082096-040::ins-2 | verified | TSA0082096-040::ins-1 | substituida per ins-2, conservada i fora de metriques |

## Que NO diu aquest report

Encara no hi ha metriques de model: aquesta versio nomes prepara el gold. Cap execucio de l'extractor forma part d'aquest report.

## Que el text no decideix

Escrit expressament, perque ningu ho ompli despres per intuicio.

### A les propostes noves

**2026SE00013::class-1**
- El grup, el subgrup i la categoria es remeten a l'apartat 7.d) del quadre de caracteristiques: no son en aquesta pagina i no s'inventen.
- L'altra branca de la disjuncio (la documentacio de l'apartat 7) es cita pero no esta anotada com a requisit propi: el grup d'alternatives te un sol membre.

**300-2025-03877::exp-1**
- El plec no dona codis CPV: diu que la similitud es determina pels tres primers digits dels codis respectius, pero no els enumera.

**300-2025-03877::award-1**
- L'EXPRESSIO de la formula no apareix al text extret: la pagina 70 acaba amb 'se aplicará la siguiente fórmula:' i la 71 comença amb les definicions de les variables. Molt probablement es una imatge que l'extraccio de text no recull.
- Sense l'expressio, aquesta anotacio pot mesurar que el model conserva les variables i la frase, pero NO que reprodueixi la formula.

**422-2026::award-1**
- La formula no defineix simbols: els operands son en llenguatge natural ('Oferta más baja', 'Oferta a valorar') i 'PL' no es defineix enlloc del text. Per aixo `formula_variables` es buida.

**TSA0082096-040::ins-2**
- Es la MATEIXA polissa que `TSA0082096-040::ins-1`, que ja esta verificada. Verificar aquesta sense retirar l'altra faria que el gold demanes dues assegurances on el plec en demana una.
- La pagina no diu ni fins quan ha d'estar vigent la polissa ni com s'acredita: `proof` i la vigencia queden sense informar.

### A les anotacions heretades

- modality: la v1 no distingia exigencia de via alternativa; queda `unknown`.
  - Afecta 11: 1-2026::exp-1, 2025-04671::cert-1, 26-095-3::ins-1, 422-2026::eco-1, 422-2026::ins-1, 4249-2026::eco-1, 4249-2026::exp-1, 4249-2026::ins-1, LIC-10495::ins-1, SER-2026-93411::eco-1, TSA0082096-040::ins-1
- equivalence_allowed: la v1 nomes ho deia en algun cas. `False` aqui vol dir 'no anotat', no 'el plec no admet equivalents'.
  - Afecta 10: 1-2026::exp-1, 26-095-3::ins-1, 422-2026::eco-1, 422-2026::ins-1, 4249-2026::eco-1, 4249-2026::exp-1, 4249-2026::ins-1, LIC-10495::ins-1, SER-2026-93411::eco-1, TSA0082096-040::ins-1
- aggregation: no consta a l'anotacio v1 verificada. Es deixa sense informar en comptes de deduir-lo del quote, que canviaria el que una persona va verificar.
  - Afecta 5: 1-2026::exp-1, 422-2026::eco-1, 4249-2026::eco-1, 4249-2026::exp-1, SER-2026-93411::eco-1
- Tipus de limit: `coverage_amount` de la v1 no distingeix total, per sinistre, per victima o agregat anual. Queda `unknown`.
  - Afecta 5: 26-095-3::ins-1, 422-2026::ins-1, 4249-2026::ins-1, LIC-10495::ins-1, TSA0082096-040::ins-1
- Sublimits: si la pagina en te, l'anotacio v1 els va aplanar en un sol import i no es poden recuperar sense tornar a anotar el cas.
  - Afecta 5: 26-095-3::ins-1, 422-2026::ins-1, 4249-2026::ins-1, LIC-10495::ins-1, TSA0082096-040::ins-1
- Llindars quantitatius (import individual o acumulat, percentatge i base): l'anotacio v1 no en portava cap.
  - Afecta 2: 1-2026::exp-1, 4249-2026::exp-1
- Valor conservat sense tipus: la forma v1 no encaixa en cap contracte tipat de la v2 sense reescriure l'anotacio.
  - Afecta 1: 2025-04671::cert-1
