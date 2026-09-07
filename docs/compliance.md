# Compliment: empresa ↔ requisits

Aquesta capa respon la primera comparació real del producte:

> **Què exigeix el plec?** (4B) vs **què pot acreditar l'empresa?**

**Cap IA.** La intel·ligència artificial ja va fer la seva feina estructurant el
plec a la Iteració 4B. Decidir si 2.450.000 ≥ 450.000, si existeix una ISO 9001
o si una pòlissa cobreix 1.000.000 € no és una tasca per a un model de
llenguatge: és una comparació, i una comparació ha de ser **explícita,
reproduïble, explicable i auditable**.

## Arquitectura

| Mòdul | Responsabilitat | Toca la BD? |
| --- | --- | --- |
| `app/compliance/rules.py` | `RULES_VERSION`, codis de regla i d'explicació | no |
| `app/compliance/profile.py` | Fotografia del perfil + fingerprint | no |
| `app/compliance/economic.py` | Solvència econòmica | no |
| `app/compliance/certifications.py` | Certificacions | no |
| `app/compliance/insurance.py` | Assegurances | no |
| `app/compliance/experience.py` | Experiència | no |
| `app/compliance/personnel.py` | Titulacions, equip i registres | no |
| `app/compliance/engine.py` | Dispatch, bloquejos, elegibilitat | no |
| `app/services/compliance_analysis.py` | Orquestració | **sí** |

Les regles són funcions pures sobre una fotografia del perfil, així que es
proven senceres sense sessió ni xarxa.

## Entitats

**De tenant**, a diferència de 4A i 4B: porten `company_id` i queden sotmeses a
l'aïllament multi-tenant. El requisit és públic; la resposta a «ho acredito?»
només té sentit per a una empresa.

```
Company + Requirement
   → CompanyRequirementAssessment → AssessmentEvidence
   → CompanyComplianceRun (agregat)
```

## No es guarda «compleix = cert»

Es guarda **per què**: la regla aplicada (`rule_code`), el motiu
(`explanation_code`), el valor del plec, el valor de l'empresa i les evidències
corporatives. Un booleà sense motiu no és auditable.

## Resultats

| Valor | Significat |
| --- | --- |
| `satisfies` | La dada estructurada demostra el compliment. |
| `does_not_satisfy` | La dada estructurada demostra que **no** es compleix. |
| `unknown` | No hi ha prou dada corporativa per decidir. |
| `needs_review` | La regla no és prou determinista; cal judici humà. |
| `not_applicable` | El requisit no aplica en aquest context. |

### La distinció crítica

`does_not_satisfy` ≠ `unknown`.

«No tenim ISO 27001 registrada» **no** és «l'empresa no té ISO 27001». Depèn de
si el perfil és complet:

* **Secció completa** → l'absència informa → `does_not_satisfy`.
* **Secció incompleta** → l'absència no demostra res → `unknown`.

Confondre-les fabrica NO-GOs falsos. S'aprofita el sistema de completitud que ja
existia (`app/services/completeness.py`).

## Bloquejos

Un bloqueig només existeix quan es donen **les tres** condicions:

```
obligation = mandatory  AND  result = does_not_satisfy  AND  prou certesa
```

| Estat | Quan |
| --- | --- |
| `none` | Qualsevol altre cas. |
| `potential` | Requisit **no verificat** + mandatory + `does_not_satisfy`. |
| `confirmed` | Requisit verificat **o** assessment revisat per una persona. |

**`unknown` i `needs_review` no bloquegen mai.** No saber no és incomplir: un
mandatory sense dades demana completar el perfil, no descartar la licitació.

### Per què `potential`

Mentre la qualitat de l'extractor real no estigui mesurada (4B es va validar
amb un proveïdor determinista, no amb un model real), deixar que una extracció
automàtica no revisada provoqui un NO-GO definitiu seria regalar falsos
negatius. La interfície diu «Possible bloqueig — revisa el requisit i la font».

## Elegibilitat i recomanació

```
confirmed ≥ 1                          → NO_GO
potential ≥ 1  o  mandatory sense resoldre → REVIEW_REQUIRED
tots els mandatory satisfies/not_applicable → ELIGIBLE
```

`eligible` **no** vol dir «guanyarem». Vol dir «no hem detectat impediments
obligatoris».

La recomanació combina elegibilitat i pre-fit **sense fusionar-los**:

| Elegibilitat | Pre-fit | Recomanació |
| --- | --- | --- |
| `no_go` | qualsevol | `no_go` |
| `review_required` | qualsevol | `review` |
| `eligible` | ≥ 60 | `go_candidate` |
| `eligible` | < 60 | `eligible_low_fit` |

## Cap mitjana

ISO obligatòria 0 + experiència 100 + import 100 **no** fan un 67 «acceptable».
Els comptadors es reporten un a un i **el bloqueig sempre domina**. Un pre-fit
de 95 amb un bloqueig confirmat continua sent un NO-GO, i el 95 es continua
veient al costat.

El pre-fit respon «ens interessa?»; això respon «podem acreditar que complim?».
Són dimensions diferents i no es barregen mai.

## Regles implementades

| Categoria | `rule_code` | Determinista quan |
| --- | --- | --- |
| Solvència econòmica | `economic_revenue_gte_v1` | hi ha import, moneda i període clar |
| Certificacions | `certification_exact_v1` | hi ha nom de norma |
| Registres | `registration_exact_v1` | el tipus és inequívoc |
| Assegurances | `insurance_coverage_gte_v1` | hi ha import i moneda |
| Experiència | `experience_count_amount_v1` | hi ha recompte i criteri objectiu |
| Titulacions | `qualification_exact_v1` | hi ha nom de titulació |
| Equip | `personnel_count_v1` | hi ha un mínim de persones |

Categories sense regla determinista (terminis, documentació administrativa,
condicions tècniques i de contracte) → `needs_review`. **No s'endevina.**

### Moneda

Sense conversió: si el plec demana euros i la dada és en una altra moneda, la
resposta és `needs_review`. No s'inventa cap tipus de canvi.

### Certificacions

Normalització segura: `ISO 9001` ≡ `ISO 9001:2015` ≡ `UNE-EN ISO 9001` (la
versió de la norma no canvia la norma). **Cap equivalència semàntica**: tenir
ENS Alt no acredita ISO 27001, encara que sovint es demanin juntes.

### Experiència i similitud

La part més delicada, i per això la més continguda:

* Amb **CPV** o **categories de servei** al requisit → filtre objectiu.
* Amb un «treballs de naturalesa similar» en text lliure → `needs_review`.

**Cap embedding, cap LLM, cap heurística de paraules clau.** «Naturalesa
similar» és precisament el que discuteixen les meses de contractació.

### Evidència d'un projecte

Un projecte no compta pel fet d'existir: ha de ser acreditable.

| Document | Força |
| --- | --- |
| Certificat de bona execució | `strong` |
| Contracte, factura, document comercial | `supporting` |
| Cap document | `missing` |

Projectes que encaixen però no tenen document → `unknown`
(`EXPERIENCE_EVIDENCE_MISSING`): és una carpeta incompleta, no un incompliment.

## Fingerprint i recàlcul

`company_profile_fingerprint` cobreix el que pot alterar un veredicte: imports,
noms, vigències, tipus, evidències i **la completitud per secció** (passar
d'incomplet a complet pot convertir un `unknown` en un `does_not_satisfy`). No
inclou notes ni descripcions.

`requirements_fingerprint` identifica l'extracció avaluada. Un
`RequirementExtractionRun` nou obliga a recalcular.

Si res ha canviat i la `rules_version` és la mateixa → es reutilitza el run
anterior. `force=true` en crea un de nou.

## Immutabilitat

Un run completat **no es modifica**. Canviar el perfil, els requisits o les
regles crea un run nou; els anteriors queden consultables tal com es van
calcular. Només un run és `is_current` per (empresa, licitació), garantit per un
índex únic parcial. Un altre índex parcial fa de lock contra el doble clic.

## Revisió manual

`automatic_result` i `reviewed_result` **conviuen**. L'efectiu és el segon si
existeix. Sense aquesta separació no es podria auditar mai si les regles
encertaven.

Revisar un assessment també pot **confirmar** un bloqueig: si una persona respon
del veredicte, ja no cal tractar-lo com a potencial.

## Criteris d'adjudicació

**No s'hi decideix cap compliment.** «45 punts d'oferta econòmica» no és un
requisit obligatori; serveix per entendre com es puntua i estimar esforç. Es
manté a part.

## Límits coneguts

* **Similitud d'experiència.** La v1 només filtra per CPV o servei explícit. La
  resta és revisió humana. És deliberat.
* **Classificació empresarial.** Grup, subgrup i categoria no es comparen: el
  perfil no els modela com a camps estructurats i decidir-ho amb text lliure
  seria endevinar. → `needs_review`.
* **Sense conversió de divisa.**
* **Processament síncron.** La lògica ja viu separada d'HTTP.
* **Qualitat de l'extractor no mesurada.** Per això els bloquejos d'un requisit
  no verificat són `potential`. Quan 4B es validi amb un model real, aquesta
  cautela es podrà revisar.
* **Un requisit sense valor estructurat** (`REQUIREMENT_VALUE_MISSING`) no es
  pot comparar. És el cas majoritari amb l'extractor de demostració actual.
