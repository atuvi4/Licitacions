# Fonts oficials d'anuncis de licitació — investigació per a Phase 3B-1

> Document companys de la spec `2026-10-06-radar-3b1-design.md`. Investigació feta el 2026-10-06 amb sondeigs mínims (cap descàrrega massiva); cada afirmació porta l'URL i la marca de verificació. La decisió final de font és a la spec §2.


**Data de comprovació de totes les afirmacions: 2026-10-06** (UTC ~07:15–07:25), si no s'indica una altra cosa.
Abast: una pime de telecomunicacions i TI de la província de Girona. Els compradors són sobretot ens locals catalans i la Generalitat, i també compradors estatals.
Mètode: documentació oficial, més **sondeigs mínims** per confirmar que les fonts existeixen: 1 pàgina de cada feed Atom de PLACSP, consultes d'agregació/`$limit=2` a SODA, 1 JSON de publicació PSCP, 1 HEAD a un PDF de plec i 1 cerca TED amb `limit=3`. No s'ha fet cap descàrrega massiva. Les mostres s'han desat al scratchpad de la sessió i no s'han publicat.

Llegenda: **[V]** = verificat avui amb una crida real · **[D]** = ho afirma la documentació oficial · **[I]** = inferència o no verificat.

---

## 0. Resum executiu

| | PSCP via Dades Obertes (Socrata `ybgg-dgi6`) | PLACSP sindicació 1044 (agregades) | PLACSP sindicació 643 (perfils propis) | TED Search API v3 | BOE / DOGC |
|---|---|---|---|---|---|
| Cobertura Generalitat + ens locals catalans | **Total (font primària)** | Subconjunt reduït (agregació) | Gairebé nul·la (només ens catalans allotjats a PLACSP) | Només per sobre de llindar | Parcial / residual |
| Estatals a Catalunya / Espanya | No | No | **Sí** | Només SARA | BOE parcial |
| Plecs (PDF) | **Sí** (via JSON per fase) | Sí (URL PSCP dins CODICE) | Sí | Rarament directes | No |
| Incremental | `:updated_at` SODA, lot T+1 | Atom paginat, ~continu | Atom paginat, ~continu | Consulta per data | Sumari diari |
| Encaix com a primera integració | **★ recomanada** | Pla B / creuament | 2a integració | 3a (enriquiment) | No |

---

## 1. PSCP: Plataforma de Serveis de Contractació Pública (Generalitat)

### 1a. Portal i API pròpia
- El portal actual és `https://contractaciopublica.cat` (el detall de cada publicació té la forma `/ca/detall-publicacio/{id_intern}/{publicacioExpedientId}`). Respon 200 [V].
- **No hem trobat cap API, RSS ni Atom documentats per a reutilitzadors** al portal. L'únic servei d'avisos és el de subscripció per correu electrònic de la PSCP, pensat per a empreses: https://web.gencat.cat/en/generalitat/accio-govern/contractacio-publica/recursos-empreses/sistema-corporatiu-contractacio-publica-electronica (resultat de cerca, 2026-10-06) [D/I].
- Sí que existeix un `portal-api` intern no documentat que el dataset oficial enllaça (vegeu 1b): `/portal-api/documents-publicacio/json-xifrat/...` i `/portal-api/descarrega-document/{id}/{hash}` [V]. No té documentació ni cap contracte de servei publicat [I].

### 1b. Dataset oficial «Contractació pública a Catalunya: publicacions a la Plataforma de serveis de contractació pública» (Socrata `ybgg-dgi6`)

| Aspecte | Detall | Font |
|---|---|---|
| Disponibilitat | Pàgina: https://analisi.transparenciacatalunya.cat/d/ybgg-dgi6 · SODA: `https://analisi.transparenciacatalunya.cat/resource/ybgg-dgi6.json` · Metadades: `/api/views/ybgg-dgi6.json` · Fitxa a datos.gob.es: https://datos.gob.es/en/catalogo/a09002970-contratacion-publica-en-catalunya-publicaciones-en-la-plataforma-de-servicios-de-contratacion-publica | [V] HTTP 200 |
| Editor | Departament d'Economia i Finances, Generalitat | metadades `/api/views/ybgg-dgi6.json` [V] |
| Freqüència | «Diària». `rowsUpdatedAt` = **2026-10-06 01:00:13 UTC**. Els lots de files porten `:updated_at` cada dia cap a les **08:00 UTC** (p. ex. 2026-10-03T08:00:23Z amb 4.722 files i 2026-10-02T08:00:21Z amb 4.537). Latència observada: un anunci publicat el 2026-10-03 a les 11:44 hora local apareix amb `:created_at` 2026-10-04T08:00Z, és a dir, **T+1** | [V] |
| Creat / publicat | Creat el 2023-02-02 i publicat el 2023-06-27. Datos.gob.es declara una cobertura temporal «des del 26/06/2023», però el `min(data_publicacio_anunci)` és 2011-02-03 | [V] |
| Volum | 1.989.643 files. 1.747.176 són «Publicació agregada de contractes» (sobretot contractes menors) i 12.589 estan en fase «Anunci de licitació». Avui hi ha 2.054 files amb `termini_presentacio_ofertes` > 2026-10-06 | [V] consultes `$group` |
| Granularitat / identificadors | «Cada fila representa: un expedient; un lot de l'expedient, per als expedients amb lots; un expedient per a les publicacions agregades» (descripció oficial). Claus: `id_intern` (UUID de l'expedient, el mateix que surt a la URL del portal) + `numero_lot`. També hi ha `codi_expedient` (codi del comprador, no és únic entre òrgans) i `codi_organ`/`codi_unitat` (ids de la PSCP). La fila és un **estat** («darrera fase publicada» = `fase_publicacio`), **no un registre d'esdeveniments** | [V] descripció de metadades |
| Comprador | `codi_dir3` (p. ex. L01171254 per a l'Ajuntament de Pardines), `codi_ine10`, `nom_organ`, `codi_ambit`/`nom_ambit` (p. ex. «Entitats de l'administració local», «Universitats»). **El NIF no és al dataset**, però sí al JSON de fase (`organ.nif`) | [V] |
| Camps (52 a la fitxa més 17 que exposa la capçalera `x-soda2-fields`) | Dates: `data_publicacio_futura/_previ/_anunci/_adjudicacio/_formalitzacio/_anul/_consulta/_avaluacio/_contracte/_encarrec` i `termini_presentacio_ofertes`. Objecte: `denominacio`, `objecte_contracte`, `descripcio_lot`. Tipus: `tipus_contracte`, `procediment`, `tipus_tramitacio`. CPV: `codi_cpv` (**un de sol**, el principal). Imports: `valor_estimat_contracte`, `valor_estimat_expedient`, `pressupost_licitacio_sense/_amb` (lot) i `_sense_1/_amb_1` (expedient). Territori: `codi_nuts` (ES512 = Girona), `lloc_execucio`. Altres: `durada_contracte`. Adjudicació: `identificacio_adjudicatari`, `denominacio_adjudicatari`, `import_adjudicacio_*`, `ofertes_rebudes`, `tipus_empresa` (pime / no pime), `resultat`. També `eina_presentacio_electronica` (p. ex. «Sobre digital»), `tipus_financament`, `racionalitzacio_contractacio`, `enllac_publicacio` i **`url_json_futura/_cpm/_previ/_licitacio/_avaluacio/_adjudicacio/_formalitzacio/_anulacio/_agregada`** | [V] capçalera `x-soda2-fields` i descripcions de columna |
| Documents (plecs) | El dataset no conté els plecs, però `url_json_licitacio` porta al JSON complet de la fase (versió «1.0.0»). Aquest JSON inclou `plecsDeClausulesAdministratives`, `plecsDePrescripcionsTecniques`, `memoriaJustificativaContracte`, `documentsAprovacio` i `altresDocuments`, amb `id`, `titol`, `hash`, `mida` i `idioma`; també solvències, garanties, criteris, obertura de sobres, `cpvPrincipal`, NUTS i durada per lot. El PDF es descarrega a `https://contractaciopublica.cat/portal-api/descarrega-document/{id}/{hash}`. Prova: HEAD → `200 application/pdf`, `content-length 619715`, que coincideix amb `mida` = 619715 del JSON | [V] 1 JSON + 1 HEAD |
| Paginació / bulk / incremental | SoQL: `$where`, `$select`, `$order`, `$limit`, `$offset`. Hi ha exportació completa en CSV/JSON/XML (`/api/v3/views/ybgg-dgi6/export.csv`, segons datos.gob.es). Incremental amb el camp de sistema **`:updated_at`** (`$where=:updated_at > 'T'`), que funciona [V]. Les files canvien **in situ** (exemple: `:created_at` 2026-07-13 i `:updated_at` 2026-10-03 quan va passar a «Adjudicació») [V] | https://dev.socrata.com/docs/app-tokens.html [D] |
| Senyal de revisió | No hi ha un historial per esdeveniment. Es dedueix de `fase_publicacio`, de les dates per fase i de `:updated_at`. Les anul·lacions surten a `data_publicacio_anul` (descripció: «desert, desistiment, renúncia, resolució de tribunal, publicació per error») i a `url_json_anulacio`. Les rectificacions dins d'una mateixa fase (p. ex. un canvi de termini) **no tenen un camp explícit** [I] | metadades de columna [V] |
| Límits / termes | Socrata: sense token, limitació per IP en un fons compartit; amb `X-App-Token`, «no throttling … unless abusive» (https://dev.socrata.com/docs/app-tokens.html, consultat 2026-10-06). Llicència: «Llicència oberta d'ús d'informació – Catalunya». Permet ús comercial i exigeix citar «Generalitat de Catalunya. Departament de …» (https://web.gencat.cat/ca/generalitat/dades-indicadors/dades-obertes/llicencies, consultat 2026-10-06). A la metadada Socrata la llicència figura com a `SEE_TERMS_OF_USE` [V] | [D] |
| Cobertura | «Tots els òrgans de contractació de Catalunya a la PSCP», és a dir, la Generalitat, el seu sector públic, els ens locals, les universitats, etc. Mostra Girona d'avui: Aj. de Pardines i Universitat de Girona [V]. Els ens locals catalans que allotgen el perfil a PLACSP i no a la PSCP (opció de l'art. 347.3 LCSP) **no hi són** [I] | descripció [V] |
| Profunditat històrica | Files des del 2011. Datos.gob.es diu «des del 26/06/2023». Hi ha un **forat conegut entre el 15 i el 19 de juny de 2026** («manquen expedients» a les versions anteriors) | descripció de metadades [V] |
| Execució | Dataset germà `8idu-wkjv` (modificacions, pròrrogues, cessions…), amb `url_json`. Respon 200 i va ser modificat el 2026-10-06 a les 01:13 UTC | [V] |

**Inconvenients coneguts de la PSCP**
1. **Latència T+1**: el dataset es carrega cap a les 08:00 UTC, de manera que el radar sempre va com a mínim un dia per darrere del portal [V].
2. Les **dates són `floating_timestamp` en hora local** sense zona: el dataset diu `termini 2026-10-26T23:59` i el JSON `2026-10-26T22:59:59Z`. Cal normalitzar a Europe/Madrid [V].
3. Hi ha **un sol CPV per fila** al dataset; els CPV addicionals només són al JSON (per confirmar) [V/I].
4. `url_json_*` és un enllaç amb un token xifrat (`json-xifrat/{id}/{token}`) que **no es pot construir**: s'ha de llegir del dataset. No sabem si el token caduca [I].
5. El JSON de fase **conté dades personals** (noms, telèfons i correus de contactes de l'òrgan). Cal fer-ne una llista blanca de camps abans d'emmagatzemar-lo (RGPD) [V].
6. El `portal-api` (JSON i descàrrega de documents) no està documentat com a API pública i pot canviar sense previ avís. També hi ha documents antics a `/portal-api/descarrega-document-antic/...` (resultats de cerca) [I].
7. Una mateixa consulta barreja expedients amb lots i sense lots: per agrupar cal fer `GROUP BY id_intern` [V].
8. Les 1,75 M de files agregades (contractes menors) s'han de filtrar (`es_agregada='NO'`) [V].

---

## 2. PLACSP: Plataforma de Contratación del Sector Público (Hisenda, DGPE)

### Fonts i documentació
- Índex de dades obertes: https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/licitaciones_plataforma_contratacion.aspx (consultat 2026-10-06). Llista 643 (perfils), agregació, contractes menors (des del 2018), encàrrecs (des del 2021), consultes preliminars (des del 2022) i l'XLSX d'òrgans: `https://contrataciondelsectorpublico.gob.es/datosabiertos/OrganosContratacion.xlsx`.
- **Especificació «Formato de sindicación y reutilización…», v1.10 del 17/09/2024, 250 pàgines**: https://contrataciondelsectorpublico.gob.es/datosabiertos/especificacion-sindicacion.pdf [V, descarregada i llegida].
- «Resumen de contenido en conjuntos de datos abiertos» (21/12/2023), matriu camp × dataset: https://contrataciondelsectorpublico.gob.es/datosabiertos/DGPE_PLACSP_ResumenDatosAbiertos.pdf [V].
- Domini: Hisenda publica ara a `contrataciondelsectorpublico.gob.es`, però els `id`/`self`/`next` del feed continuen apuntant a `contrataciondelestado.es` [V].

| Aspecte | 643 `licitacionesPerfilesContratanteCompleto3` | 1044 `PlataformasAgregadasSinMenores` |
|---|---|---|
| URL viva | `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom`. **[V] 200**, `Last-Modified` dl. 05/10/2026 18:19 GMT, 398 entrades a la pàgina 1 (11,9 MB) | `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores.atom`. **[V] 200**, `Last-Modified` dt. 06/10/2026 01:00 GMT, 226 entrades (2,8 MB) |
| Pàgines de documentació | https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/LicitacionesContratante.aspx | https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/LicitacionesAgregacion.aspx |
| Bulk | ZIP anuals `…_YYYY.zip` (2012–2026) i mensuals `…_YYYYMM.zip` de l'any en curs. «El fichero del mes actual contiene la actualización hasta el día previo» [D] | ZIP `…_AAAAMM.zip` i anuals des del 2016 [D] |
| Cobertura | Òrgans amb perfil **allotjat a PLACSP**: AGE i sector públic estatal (a la mostra: Autoritat Portuària de Barcelona, Sincrotró ALBA, ISM Tarragona, mútues com EGARSAT, TRAGSA) i els ens locals que s'hi han adherit [V mostra] | Plataformes autonòmiques que **agreguen** un «conjunt reduït de dades» a PLACSP. Hosts a la mostra: **contractaciopublica.cat (123 de 226)**, Euskadi (42), Andalusia (29), Madrid (20), Navarra (8) i Galícia (4) [V] |
| Identificadors | `entry/id` = `…/licitacionesPerfilContratante/{n}` (id de l'expedient a PLACSP). `ContractFolderID` = número d'expedient. Lot: `ID schemeName="ID_LOTE"`. Òrgan: `DIR3`, `NIF` i `ID_PLATAFORMA`. També `TED` (id únic TED, §4.49) [V] | `entry/id` = `…/PlataformasAgregadasSinMenores/{n}`. Òrgan **només** `ID_OC_PLAT` (id de la PSCP, p. ex. 8374426) i plataforma `AgentParty/ID` = 62 «Generalitat de Catalunya». **Sense NIF ni DIR3** a l'agregació (Resumen 4.17: NIF «No» per a Agregadas). `entry/link` = URL PSCP amb `id_intern`, cosa que permet el **join amb `ybgg-dgi6`** [V] |
| Paginació | RFC 5005 «Paged Feeds», 500 entrades per fitxer com a màxim, `rel="next"` (p. ex. `…_20261005_201859.atom`), ordenat per `updated` de més nou a més antic (§3.2) [D/V] | igual [V: `next` = `…_20261006_030035.atom`] |
| Semàntica d'actualització | Cada anunci nou genera una **nova `<entry>` amb el mateix `id`**. Només val la més recent per `updated` (§3.3.2). Les correccions excepcionals d'errors **no canvien `updated`** (§3.3.1). Es poden purgar entrades antigues; «siempre existirá la versión más reciente» [D] | igual [D] |
| Cancel·lacions / baixes | `<at:deleted-entry ref when>` (RFC 6721) amb `<at:comment type="ANULADA"/"CERRADA">` des de juliol de 2022 (§3.1.2). A la pàgina 1 [V]: 2 `deleted-entry` (ANULADA) | A la pàgina 1 [V]: 76 `deleted-entry` (p. ex. CERRADA). L'estat de l'expedient és `ContractFolderStatusCode` (PRE/PUB/EV/ADJ/RES/ANUL) |
| Camps | Molt complets: estat, objecte, VE, pressupost sense/amb IVA, CPV (també per lot), tipus/subtipus, procediment, tramitació, terminis, NUTS (`CountrySubentityCode`), lots, solvència, criteris, garanties, resultat i adjudicatari, `ValidNoticeInfo` (historial d'anuncis amb tipus i data) [D Resumen] | **Reduït**: estat, expedient, objecte, imports, CPV, tipus, NUTS, procediment, terminis i resultat bàsic. **Sense** solvència, criteris, garanties ni NIF de l'òrgan; el CPV per lot només s'hi va afegir el 15/12/2023 [D Resumen]. **Sí que inclou plecs**: `LegalDocumentReference/TechnicalDocumentReference` → `https://contractaciopublica.cat/portal-api/descarrega-document/{id}/{hash}` [V] |
| Documents | `…/wps/wcm/connect/PLACE_es/Site/area/docAccCmpnt…` i `…/FileSystem…` (3.833 URI a la pàgina 1) [V] | URL de la PSCP (vegeu més amunt) [V] |
| Límits / termes | Sense límits de freqüència publicats. Reutilització: «Origen de los datos: Ministerio de Hacienda», ús comercial permès, condicions a datos.gob.es/avisolegal (https://www.hacienda.gob.es/es-ES/Paginas/Avisolegal.aspx, consultat 2026-10-06) [D] | igual |
| Profunditat | des de l'1/1/2012 [D] | des de l'1/1/2016 [D] |

**Inconvenients coneguts de PLACSP**
1. Les entrades es dupliquen per id: cal desduplicar per `updated` màxim. Les correccions excepcionals no mouen `updated`, de manera que un incremental «per data» les perd [D].
2. Els camps varien segons la data de l'última actualització (marques (1)–(5): 2018, 2021, 22/10/2021, 15/09/2022, 15/12/2023) [D Resumen].
3. Les pàgines són grans (12 MB per 400 entrades) i el CODICE té espais de noms `urn:dgpe:names:draft:codice…`. Cal un parser XML amb streaming [V].
4. A l'agregació catalana falten el NIF i el DIR3 de l'òrgan i el detall de solvència i criteris; tot això és a la font primària (la PSCP) [V/D].
5. Domini doble (`contrataciondelsectorpublico.gob.es` / `contrataciondelestado.es`): convé tractar els `id` com a cadenes opaques [V].
6. Les zones horàries van en hora local peninsular amb offset (+02:00) [V/D].

---

## 3. TED: Tenders Electronic Daily, Search API v3

| Aspecte | Detall | Font |
|---|---|---|
| Disponibilitat | `POST https://api.ted.europa.eu/v3/notices/search`. **[V] 200** amb una consulta de prova (2026-10-06 07:21 UTC). Swagger: https://api.ted.europa.eu/swagger | https://docs.ted.europa.eu/api/latest/index.html (2026-10-06) |
| Autenticació / versions | «The Search API does not require a key». La v3 és la vigent i «TED API v2 will remain supported until version 4 becomes available». eForms en producció des del 14/11/2022 | ídem [D] |
| Paginació | `PAGE_NUMBER` amb un màxim de 15.000 avisos per consulta; `ITERATION` (scroll) amb `iterationNextToken` i sense límit; màxim de 250 avisos i 10.000 camps per pàgina | https://docs.ted.europa.eu/ODS/latest/reuse/search-api.html [D] |
| Rate limits | **No n'hi ha d'oficials a les pàgines consultades.** Fonts no oficials parlen d'una política de «fair use» de 700 peticions/minut per IP i d'errors 429 amb ràfegues | [I] |
| Identificadors | `publication-number` (p. ex. `600121-2026`) per avís. eForms té els seus propis ids de procediment i lot. PLACSP enllaça amb l'id TED (§4.49) | [V/D] |
| Camps | Consulta experta (`query`) i `fields` (p. ex. `notice-type`, `buyer-name`, `buyer-city`, `classification-cpv`, `place-of-performance`, `deadline-receipt-tender-date-lot`, `links` amb XML/PDF/HTML per idioma) | [V] |
| Documents | Avís en XML/PDF/HTML. Els plecs **no** es donen directament; com a molt, un enllaç a la plataforma nacional [I] | [V links] |
| Cobertura | Només contractes per sobre de llindar (DOUE). Una consulta `place-of-performance IN (ES512)` des del 2026-09-01 retorna **362**, però les primeres respostes són d'abast **estatal** (llistes de totes les NUTS d'Espanya) amb compradors de Madrid o Sevilla → **soroll alt** per a un filtre territorial [V] | — |
| Encaix per a la pime | Baix com a font primària: la majoria de licitacions TIC locals de Girona queden per sota de llindar. És útil com a enriquiment o creuament per a SARA | [I] |

---

## 4. BOE / DOGC (secundàries)

| | BOE | DOGC |
|---|---|---|
| Accés | API oberta de sumaris: `GET https://www.boe.es/datosabiertos/api/boe/sumario/{aaaammdd}` (XML/JSON), doc. `https://www.boe.es/datosabiertos/documentos/APIsumarioBOE.pdf` (https://www.boe.es/datosabiertos/api/api.php, 2026-10-06) [D] | Dades obertes del DOGC: https://dogc.gencat.cat/ca/serveis/Dades_obertes/index.html (Akoma Ntoso/RDF/ELI) [D, cerca] |
| Valor per al radar | Des de la LCSP, la publicitat de referència és el **perfil de contractant** (PLACSP/PSCP), i el BOE/DOGC només recull un subconjunt d'anuncis. No inclou plecs ni dades estructurades de lot/CPV [I] | ídem [I] |
| Recomanació | Descartades com a primera integració | — |

Altres fonts vistes però no avaluades: el dataset de l'AOC `https://dadesobertes.seu-e.cat/dataset/css-rc-contractes-pscp` (registre de contractes, no són anuncis) i la sindicació de contractes menors de PLACSP.

---

## 5. Recomanació: primera integració real

**Font: PSCP via Dades Obertes de Catalunya, dataset Socrata `ybgg-dgi6` (SODA), amb el JSON de fase (`url_json_licitacio`) per als plecs.**

Raonament:
1. **Cobertura on hi ha el negoci.** És la font primària oficial de *tots* els òrgans de contractació catalans a la PSCP (Generalitat, ens locals, universitats, consorcis), també els de Girona (hi ha `codi_nuts=ES512`, DIR3 i INE10). Les altres fonts són derivades (1044) o no cobreixen aquest segment (643, TED).
2. **Esforç d'integració mínim.** Una API JSON amb consultes filtrables (`$where` per NUTS, CPV, termini i fase), un incremental natiu (`:updated_at`), una fila estable per expedient/lot (`id_intern` + `numero_lot`) i cap XML CODICE de 12 MB per pàgina.
3. **Utilitat per a la pime.** Inclou imports sense i amb IVA, procediment, termini, CPV, eina de presentació i enllaç directe. Via el JSON de fase s'obtenen els **PDF dels plecs** (PCAP/PPT amb hash i mida), la solvència i les garanties, que són l'entrada natural del motor d'anàlisi existent.
4. **Llicència clara** (ús comercial amb atribució) i un editor institucional (Economia i Finances).
5. Quan calgui, el creuament amb PLACSP és barat: l'`entry/link` de la 1044 conté el mateix `id_intern`.

**Segona integració (estatals):** PLACSP 643 (Atom/CODICE), filtrant per NUTS ES51x o per òrgans d'interès. **Tercera:** TED, com a enriquiment SARA.

### Què cal resoldre amb un spike abans de comprometre's (sense descàrregues massives)
1. **Un dia complet de Girona**: `$where=:updated_at >= '2026-10-0XT00:00' AND codi_nuts like 'ES512%' AND es_agregada='NO'` en dos dies consecutius. Comptar les altes i canvis de fase i comprovar que `id_intern + numero_lot` és únic (la consulta `$having` d'avui no ha retornat duplicats, però l'hem de repetir amb `count(distinct)`).
2. **Rectificacions dins de fase**: trobar un expedient amb una correcció de termini o de plecs. Cal veure si canvia `:updated_at`, si `termini_presentacio_ofertes` s'actualitza i si el JSON de fase reflecteix un document nou.
3. **Vida del token `url_json_*`**: tornar a llegir el mateix JSON després de 24–48 h i després d'un canvi de fase.
4. **Descàrrega de plecs**: confirmar el patró `descarrega-document/{id}/{hash}` en 3–5 expedients (sense lots i amb lots) i el comportament de `descarrega-document-antic`.
5. **Cobertura**: comparar el dataset amb la 1044 per a ES512 durant 1 setmana (via `id_intern`), i comprovar si hi ha ens locals de Girona allotjats directament a PLACSP (OrganosContratacion.xlsx), que caldria cobrir amb la 643.
6. **CPV addicionals i lots**: verificar al JSON on són els CPV secundaris i com s'hi representen els lots (`dadesPublicacioLot[]`).
7. **Token Socrata**: demanar un `X-App-Token` i fixar un ritme prudent (1 petició/s).
8. **Dades personals**: definir una llista blanca de camps del JSON (excloure `personesContacte`).

---

## 6. Confiança / no verificat

**Alta confiança (verificat avui amb crides reals):** existència i frescor de `ybgg-dgi6` i `8idu-wkjv`; la llista de columnes, inclosos `url_json_*` i `id_intern`; la càrrega diària cap a les 08:00 UTC amb latència T+1; el funcionament de l'incremental `:updated_at`; que el JSON de fase conté els plecs i que el PDF es descarrega (HEAD 200); que els feeds 643 i 1044 responen, amb `next`, `deleted-entry` ANULADA/CERRADA i entrades catalanes a la 1044 que enllacen amb la PSCP i porten els plecs; que la Search API de TED respon sense clau i el soroll de `place-of-performance`.

**No verificat / inferències:**
- Que no existeixi cap API, RSS o Atom oficial de la PSCP per a reutilitzadors. Només s'ha fet cerca web; la nota de premsa de govern.cat ha retornat 403.
- L'estabilitat i els termes d'ús del `portal-api` de contractaciopublica.cat (no està documentat).
- La caducitat dels tokens `json-xifrat`.
- Si les rectificacions dins d'una mateixa fase es reflecteixen al dataset o al JSON.
- L'abast real de les entitats locals catalanes allotjades a PLACSP en lloc de la PSCP.
- La profunditat històrica fiable de `ybgg-dgi6` (datos.gob.es diu 2023; les dades arriben al 2011).
- Els rate limits de TED (només fonts no oficials) i si TED exposa els enllaços als plecs.
- El valor de BOE/DOGC com a font d'anuncis (no s'han mostrejat).
- La freqüència exacta de regeneració dels feeds PLACSP: la documentació diu «diaria», però s'han observat diverses pàgines per dia.
- La línia de temps del canvi de domini contrataciondelestado.es → contrataciondelsectorpublico.gob.es (els dos funcionen avui).
