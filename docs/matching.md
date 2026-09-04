# Pre-fit determinista sobre metadades

Aquest document explica com es calcula l'encaix entre el perfil d'una empresa i
una licitació, i **per què cada regla és com és**. Els números concrets viuen
tots a `apps/api/app/matching/rules.py`; aquí hi ha el raonament.

---

## 1. Què és i què no és

El resultat d'aquest mòdul es diu **pre-fit**, i el nom és deliberat.

| No és | Per què |
| --- | --- |
| Una probabilitat de guanyar | No sabem res dels competidors, ni dels criteris d'adjudicació, ni de la solvència exigida. |
| Un Go / No-Go | Un Go/No-Go es decideix llegint els plecs. Aquí no s'ha obert ni un PDF. |
| Una recomanació | Diu si val la pena **mirar-s'ho**, no si val la pena presentar-s'hi. |

El pre-fit només mira **metadades estructurades publicades per la font**: codis
CPV, codi NUTS i nom del lloc d'execució, import, estat i termini. Res més.

No hi intervé cap LLM, cap embedding i cap mesura de similitud semàntica. El
motor és una funció pura: mateixes entrades, mateixa sortida, sempre. Això és el
que fa que es pugui provar, auditar i explicar a un client que pregunta «per què
m'has ensenyat això?».

---

## 2. Estructura del mòdul

```
app/matching/
  rules.py       Tots els números i codis de motiu. Cap constant fora d'aquí.
  profile.py     Instantània del perfil de l'empresa (una càrrega, no N+1).
  cpv.py         \
  geography.py    |  Una dimensió cadascun. Funcions pures.
  economic.py     |
  timing.py      /
  engine.py      Combina les dimensions en un resultat explicable. Sense BD.
  service.py     Persistència, lots, recàlcul. L'única capa que toca la BD.
  __main__.py    CLI: python -m app.matching --company <uuid> | --all-companies
```

La separació entre `engine.py` i `service.py` no és decorativa: permet provar
tota la lògica de puntuació sense base de dades ni HTTP, i és per això que els
tests del motor corren en dècimes de segon.

---

## 3. Les quatre dimensions

| Dimensió | Pes | Què compara |
| --- | --- | --- |
| CPV | 40 | Codis CPV de la licitació ↔ CPV declarats per l'empresa |
| Territori | 25 | NUTS / lloc d'execució ↔ territoris declarats |
| Import | 25 | Valor estimat o pressupost base ↔ capacitat de contractació |
| Termini | 10 | Estat i data límit ↔ temps per preparar una oferta |

Els pesos sumen 100 i hi ha un `assert` al mòdul que ho comprova en importar-lo.

**Per què el CPV pesa el doble que la resta:** és l'única dimensió que parla del
*què*. Una empresa pot ampliar territori, buscar finançament o córrer amb el
termini; el que no pot és presentar-se a un contracte d'una activitat que no fa.

### 3.1 CPV — jerarquia per prefix del codi

El CPV és jeràrquic per construcció: `45314320` (cablejat informàtic) és una
especialització de `45314300` (infraestructura de cablejat), que ho és de
`4531xxxx`, i així fins a la divisió `45` (obres de construcció). La coincidència
es mesura pel **prefix comú més llarg**:

| Dígits comuns | Nivell | Factor |
| --- | --- | --- |
| 8 | Codi exacte | 1,00 |
| 5 | Categoria | 0,75 |
| 4 | Classe | 0,55 |
| 3 | Grup | 0,35 |
| 2 | Divisió | 0,20 |

Això surt de l'estructura oficial del codi. **No hi ha cap taula d'equivalències
inventada**: no direm mai que un CPV «s'assembla» a un altre si no comparteixen
prefix.

El factor es multiplica després per la prioritat que l'empresa ha donat al CPV
(alta 1,00 / mitjana 0,85 / baixa 0,70), i cau a 0,30 si l'únic CPV que coincideix
està desactivat.

**Sostre per CPV.** Una divisió compartida (2 dígits) no vol dir gran cosa:
`45` cobreix des de cablejat fins a asfaltat de carreteres. Per això, si la
millor coincidència és de divisió o inferior, la licitació **no pot ser
candidata** encara que sumi molts punts per territori, import i termini. Aquest
sostre (`CPV_CANDIDATE_BLOCKERS`) es va afegir després d'observar que un
expedient sense cap CPV en comú arribava exactament al llindar de 60 sumant
només les altres tres dimensions. Vegeu §7.

### 3.2 Territori — codi NUTS o nom exacte

Dues vies, per ordre de fiabilitat:

1. **Codi NUTS.** El codi declarat per l'empresa ha de ser prefix del de la
   licitació. `ES51` (Catalunya) cobreix `ES512` (Girona). És la jerarquia
   oficial d'Eurostat, no una suposició.
2. **Nom exacte normalitzat.** Sense accents ni majúscules, comparació exacta.
   La font publica sovint el nom bilingüe (`Valencia/València`,
   `Gipuzkoa/Guipúzcoa`), així que el nom es parteix per `/` **abans** de
   normalitzar, i qualsevol de les parts val.

No hi ha comparació difusa. Un nom o coincideix o no coincideix.

**Territori no declarat ≠ territori prohibit.** Si la licitació cau fora dels
territoris de l'empresa, la dimensió puntua poc (25 × 0,25) però no exclou res:
una empresa declara *preferències*, no prohibicions, i una oportunitat molt bona
fora de zona ha de poder arribar a la pantalla — sempre per darrere de les del
seu territori. Només si l'empresa ha marcat explícitament `willing_to_work =
false` per a aquell territori es converteix en exclusió.

### 3.3 Import — capacitat, no solvència

L'import es compara amb els cinc llindars que l'empresa configura al seu perfil:

| Tram | Factor |
| --- | --- |
| Dins la franja preferida | 1,00 |
| Per sobre del mínim interessant | 0,75 |
| Per sobre de la franja preferida | 0,60 |
| Per sobre del màxim recomanat | 0,30 |
| Per sota del mínim interessant | 0,15 |
| Per sobre del màxim assumible | **exclusió** |

Cal insistir-hi perquè és fàcil de confondre: això **no és solvència econòmica**.
No diu que l'empresa no pugui optar legalment al contracte; diu que supera el
màxim que ella mateixa ha declarat com a assumible. És una preferència de negoci
i es pot canviar al perfil.

Un `0` a l'import es tracta com a **absent**, no com a contracte de zero euros:
la font el fa servir sovint com a marcador de «no informat».

### 3.4 Termini — dues coses diferents

- **Estat no accionable** (`awarded`, `resolved`, `cancelled`) o **termini
  vençut** → exclusió d'estat: no és que no encaixi, és que ja no s'hi pot
  presentar ningú.
- **Termini obert** → punts segons els dies que queden: còmode (>20), adequat
  (>10), curt (>5), crític. Un termini crític **no exclou**: hi ha empreses que
  s'hi presenten igualment i la decisió és seva.

---

## 4. El tractament de «no ho sabem»

Aquest és el punt que més decisions ha necessitat, i el que més diferencia el
resultat d'un score ingenu.

> **«No tenim la dada» no és el mateix que «no encaixa».**

Si la font no publica l'import, donar 0 punts a la dimensió econòmica seria
penalitzar l'empresa per un buit de la font. Donar-li el màxim seria premiar-la
pel mateix motiu. Les dues coses són mentida.

La solució: **una dimensió desconeguda queda fora del càlcul**, tant del
numerador com del denominador.

```
score = round( punts_obtinguts / pes_avaluable * 100 )
data_coverage = pes_avaluable / 100
```

Un encaix perfecte del qual només sabem CPV i territori dona **100 amb una
cobertura del 65 %**, no 65. El primer diu «encaixa amb el que sabem»; el segon
diria «encaixa a mitges», que és fals.

Si la cobertura baixa del 50 %, el resultat no és una puntuació dolenta sinó un
estat propi: `insufficient_data`. La interfície ho diu amb aquestes paraules,
perquè l'usuari sàpiga que ha de mirar-s'ho ell.

---

## 5. Estats del resultat

| Estat | Quan | Té puntuació |
| --- | --- | --- |
| `candidate` | ≥ 60 punts i amb coincidència CPV de grup o millor | sí |
| `low_fit` | Puntua, però per sota del llindar o amb el sostre de CPV aplicat | sí |
| `excluded` | Exclusió **de negoci**: preferència de l'empresa | no |
| `closed` | Exclusió **d'estat**: fora de termini o ja adjudicada | no |
| `insufficient_data` | Cobertura de dades < 50 % | no |

Deliberadament **no hi ha `GO` ni `NO_GO`**: aquests estats només es podran
calcular quan s'hagin llegit els plecs. Hi ha un test que ho comprova
(`test_cap_terme_prohibit_en_aquesta_fase`).

L'ordre de precedència és: estat → exclusió de negoci → puntuació. Una licitació
adjudicada surt com a `closed` encara que també superés el màxim assumible: el
motiu més útil per a l'usuari és el que fa que no s'hi pugui presentar.

---

## 6. Lots

Un expedient amb lots es puntua **lot per lot**, i el resum de l'expedient és el
**millor lot, mai la mitjana**. Amagar un lot que encaixa al 91 darrere d'una
mitjana de 55 seria perdre justament la informació útil. El recompte de lots
candidats (`2 de 3 lots candidats`) acompanya sempre el número.

Herència de dades del lot cap a l'expedient:

| Camp | Hereta? | Per què |
| --- | --- | --- |
| CPV | sí | Un lot sense CPV propi pertany a l'objecte general del contracte. |
| NUTS / lloc | sí | Mateix motiu. |
| **Import** | **no** | Un lot de 200.000 € dins un expedient de 5 M € no és un contracte de 5 M €. Heretar l'import faria excloure lots perfectament assumibles. |

---

## 7. Dos defectes trobats analitzant la distribució real

Els dos es van detectar executant el motor sobre 1.188 licitacions reals i
mirant la distribució, no llegint el codi.

**7.1 El llindar era assolible sense cap CPV en comú.** Amb territori (25),
import (25) i termini (10) al màxim, una licitació sense cap coincidència de CPV
arribava a 60 sobre 60 punts avaluables → exactament el llindar de candidata.
Una empresa de telecomunicacions hauria vist «Renovación de licencias Oracle»
com a candidata perquè és a Barcelona i costa el que li agrada. Correcció: el
sostre per CPV de §3.1.

**7.2 Territori no declarat puntuava igual que territori rebutjat.** Els dos
donaven 0 de 25, tractant «no ho has dit» com «has dit que no». Correcció: el
factor 0,25 de §3.2.

Cap dels dos es va corregir tocant pesos per obtenir una distribució més
agradable. Els pesos no s'han mogut.

---

## 8. Recàlcul i obsolescència

El pre-fit es persisteix (`company_tender_matches`, `company_tender_lot_matches`)
perquè el feed ha de poder ordenar i paginar per puntuació dins la base de dades.

Cada fila desa `company_profile_changed_at`, `tender_changed_at` i
`rules_version`. En llegir, es compara amb l'estat actual: si el perfil ha
canviat, la licitació ha rebut una revisió, o la versió de regles és una altra,
la fila es marca com a **desactualitzada** a la interfície. No hi ha cap sistema
d'esdeveniments al darrere; per aquest volum, comparar marques de temps és
suficient i no s'ha de mantenir.

El recàlcul és idempotent: crea el que falta i actualitza la resta. Recalcular
dues vegades seguides no duplica res.

**Rendiment mesurat** (PostgreSQL local, 1.188 licitacions i 1.017 lots):
~1,2 s per empresa, amb el perfil carregat una sola vegada (4 consultes) i les
licitacions en una sola consulta amb `selectinload`.

---

## 9. Multi-tenant

El pre-fit **és una dada de tenant**, a diferència de la licitació, que és
pública i global:

- `company_id` obligatori a totes dues taules.
- Els endpoints pengen de `/companies/{company_id}/matches` i validen la
  pertinença.
- El filtre per empresa forma part de la consulta fins i tot quan es demana un
  match pel seu UUID: sense això, conèixer un identificador permetria llegir el
  pre-fit d'una altra empresa.
- Cross-tenant retorna **404, no 403**: no confirmem ni que l'empresa existeixi.

`Tender` continua sense cap `company_id`: la mateixa licitació la veuen tots els
tenants, cadascun amb el seu propi pre-fit.

---

## 10. Explicabilitat

Cada resultat desa un array `factors` amb, per dimensió: el resultat
(`match` / `partial` / `no_match` / `unknown`), el codi de motiu, els punts, el
pes, el valor del perfil i el valor de la licitació que s'han comparat.

Cada codi de motiu té una frase en català a `apps/web/src/lib/matching.ts`, i hi
ha un test que falla si algun codi del backend no en té
(`test_tots_els_codis_tenen_etiqueta_al_frontend`). El valor del pre-fit no és el
número: és poder dir per què.

---

## 11. Límits coneguts

- **Només metadades.** Si un plec exigeix una classificació empresarial concreta
  o una experiència mínima, això no es veu aquí.
- **La qualitat depèn de la font.** Si PLACSP no publica el CPV o el lloc
  d'execució, la dimensió queda com a desconeguda.
- **La comparació de topònims és exacta.** Un municipi escrit d'una manera
  inusual no coincidirà. És preferible a inventar coincidències.
- **El territori es compara amb el lloc d'execució**, no amb la seu de
  l'organisme contractant.
- **Els pesos són globals**, iguals per a totes les empreses. Personalitzar-los
  és una decisió de producte pendent, no una limitació tècnica.
