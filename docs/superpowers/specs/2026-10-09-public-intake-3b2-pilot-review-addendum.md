# Phase 3B-2 — Addendum d'acceptació: revisió manual del pilot real (2026-10-09)

Font: revisió manual del propietari sobre el pilot (507ac57, DB 0057, dades reals) el 2026-10-08/09. Aquest addendum **no canvia cap decisió d'arquitectura** de la spec `2026-10-07-public-intake-3b2-design.md`: classifica cada observació, diu on es corregeix (Task 9 = fix round 1 + lots 9A/9B; Task 10; residual) i fixa el criteri d'acceptació. Les correccions són d'interfície, copy o guards ja previstos; cap migració nova.

Llegenda: **MUST** (bloqueja l'acceptació de 3B-2) · **SHOULD** (es fa si no implica canvi d'arquitectura; si no, residual explícit).

## 1. Fitxa de licitació, Plecs, anàlisi (lot 9A, dins Task 9)
| # | Observació | Classificació | Correcció / criteri d'acceptació |
|---|---|---|---|
| A1 | Objecte mostra metadata crua («Id licitación… Órgano… Importe… Estado…») | MUST · ja a §7.1.2 | `description=None` + `feed_summary`; UI «Objecte no publicat al feed» (font) / «Sense descripció» (manual). Backfill CLI abans del primer sync (runbook). |
| A2 | Historial barreja canvis de la font/òrgan amb activitat interna (`document_processing`) | MUST · ja a §7.1.1 | Seccions «Publicat per la font» / «Processament intern (Tuvi)»; `origin` del backend; mai «l'òrgan» en `internal`; cap codi cru ni URL. |
| A3 | Revisions internes presentades com «Versió 2/3» de la licitació oficial | MUST · nou | La numeració «Versió N» només compta revisions `source`; les `internal`/`manual` no porten número de versió oficial (etiqueta «Processament intern» + data). |
| A4 | «Plecs (2)» implica paquet complet | MUST · ja a §7.1.5 | Capçalera «N de M documents analitzables llegits» + línia de cobertura del paquet (`package_status`: desconeguda / incompleta amb gaps / completa) + origen de cada document. |
| A5 | «Aquest plec encara no s'ha llegit» després d'extreure text | MUST · nou (copy) | Estats del document: «Sense text extret» → «Text extret · requisits encara no analitzats» → «Analitzat». |
| A6 | «0 de 0 requisits obligatoris» abans d'extreure | MUST · ja a §7.1.3 | `analysis_state=absent` → «Requisits encara no analitzats» (mai fracció). |
| A7 | «Analitza compliment» / anàlisi v3 sense requirement run vigent | MUST · guard | Backend: compliment → 422 `no_requirements_to_assess` (existent), v3 → 409 `analysis_prerequisites_missing` (Task 5); tests explícits dels dos; UI: CTAs amagades/desactivades si `analysis_state != current`. |
| A8 | CTAs «Analitza» duplicades | MUST · nou (UI) | Una sola CTA primària per pantalla (fitxa: «Analitza el plec» a la capçalera; Plecs: cap duplicat). |
| A9 | Pre-fit «Sense calcular» repetit massivament a Totes | MUST · ja a §7.1.4 | A la llista, cap text per fila quan no està calculat; un sol avís a la capçalera de la llista amb la condició del perfil. |
| A10 | A la fitxa no s'ha d'obligar a recalcular el pre-fit global | MUST · ja a §7.1.4 | «Calcula'l ara» per a aquest tender (`POST /matches/recalculate?tender_id=`). |
| A11 | Countdown inconsistent (12 dies a fitxa/Oportunitats, 11 a Candidatures) | MUST · nou | Una sola funció web `deadlineCountdown(deadline, now)` (regla: dies naturals sencers restants a Europe/Madrid, arrodoniment únic) usada a TOTES les pantalles; vitest amb el cas del pilot. |
| A12 | «En termini» quan el termini ja ha passat (estat cru antic) | MUST · nou | Estat derivat: `deadline < now` → «Fora de termini» a totes les pantalles, independentment de l'estat cru de la font (que es mostra a part com «Estat a la font: …»). |
| A13 | «En anàlisi» ambigu sense cap anàlisi real | MUST · nou (etiqueta) | Etiqueta del workspace segons `analysis_state`: `absent` → «Pendent d'anàlisi», `stale` → «Anàlisi obsoleta», `current` → «En anàlisi». El valor d'estat no canvia. |
| A14 | Semàntica de recomanació automàtica GO/NO-GO a Candidatures | MUST · nou (copy) | Cap «Recomanació»: la decisió és humana; el resultat del motor es mostra com «Resultat de l'anàlisi» i l'estat com «Pendent de decidir / GO / NO-GO». |
| A15 | «Tancar · 1 cosa per resoldre» vs «3 pendents» incoherent | MUST · nou | Un sol comptador (mateixa font de dades) a la capçalera del pas i a la llista. |
| A16 | «Veure la licitació» difícil de trobar | MUST · nou (UI) | Enllaç persistent a la capçalera del workspace, separat dels badges, present a Decidir/Preparar/Tancar. |
| A17 | «0 oportunitats noves» ha d'explicar que són prioritzades pel Radar | MUST · Task 10 (§7.2) | Dashboard: «candidates» + `total_tenders` + causa; copy «prioritzades pel Radar, no licitacions ingerides». |
| A18 | Preparar pre-GO visible/read-only, cap mutació/generació, guard backend | MUST · Task 10 (§7.1.6) | `assert_workspace_go` 403 `decision_required`; UI lectura. |

## 2. Perfil i coneixement d'empresa (lot 9B, dins Task 9; només UI/copy)
| # | Observació | Classificació | Correcció / criteri |
|---|---|---|---|
| B1 | «24 % de completitud» i semàntica d'onboarding/ERP | MUST | Eliminar el mesurador de completitud i la copy d'onboarding; cap percentatge global. |
| B2 | Totes les dades declarades com «pendents per acreditar» | MUST | «Declarat» és un estat vàlid; l'evidència només es demana quan una licitació la requereix (cap tasca global «per acreditar»). |
| B3 | KPI global «Desconegut 0» | MUST | Eliminar. |
| B4 | Preferències Radar (territori/CPV) barrejades amb coneixement corporatiu | MUST | Secció/pantalla «Preferències del Radar» separada de «Coneixement»; sense canvi de model. |
| B5 | Coneixement convertit en 38 tasques globals | MUST | Cap llista de tasques global; estats declarat/verificat/històric es conserven a cada ítem. |
| B6 | Coneixements sensibles (FTTH pròpia, Tier-3) sense abast/cobertura/partner visibles | MUST | Mostrar la línia d'abast (cobertura territorial, partner, completesa) a l'ítem; «Sense especificar» quan no hi ha dada. |
| B7 | Experiència: històric de licitacions ≠ referències d'execució acreditables | MUST | Dues seccions amb etiquetes diferents: «Històric de licitacions» i «Referències acreditables». |

## 3. Radar, cercador i filtres (lot 9B, dins Task 9; UI + contracte del cercador)
| # | Observació | Classificació | Correcció / criteri |
|---|---|---|---|
| C1 | Filtre Lloc d'execució = L'Escala dona 0 (1044 sovint només té NUTS) | MUST | Etiqueta «(cobertura parcial)» i empty state que explica que moltes licitacions només indiquen NUTS i suggereix filtrar per NUTS. |
| C2 | NUTS amb nom humà («Girona (ES512)») | MUST si hi ha mapping | Si el repositori té un mapping oficial NUTS→nom, usar-lo; si no, mostrar el codi i registrar-ho com a residual (no inventar noms). |
| C3 | Filtre CPV: prefix funciona, paraula no, tot i prometre «o paraula» | MUST | Contracte corregit: autocomplete codi+descripció (endpoint de cerca de CPV existent o nou `GET /cpv?q=`), i el filtre aplica sempre el codi triat. |
| C4 | Fila que entra per CPV secundari: mostrar quin CPV ha fet match | MUST | Client-side: destacar el CPV de la fila que coincideix amb el filtre. |
| C5 | Xips dels filtres actius | MUST | Xips amb «×» per a cada filtre actiu. |
| C6 | Cerca textual amb soroll: ordre per rellevància sense ocultar resultats | MUST si l'API ho permet | Si el cercador exposa ordenació per rellevància, oferir-la quan hi ha query; si no, residual explícit. |
| D1 | Cerca automàtica amb debounce + Enter | SHOULD | Debounce 300 ms + Enter. |
| D2 | «Totes \| Per revisar» → «Totes \| Radar/Prioritzades» | SHOULD | Renombrar. |
| D3 | «Analitzar» a la targeta d'Oportunitats és ambigu | SHOULD | «Obre la fitxa». |
| D4 | CPV amb nom oficial, no codi duplicat | SHOULD | Nom de `cpv_codes` si existeix. |
| D5 | Traçabilitat tècnica (hashes, SourceRecord) plegable | SHOULD | Bloc plegable «Detalls tècnics». |
| D6 | Aclarir «Marca com revisats», «v1», «Vincula» | SHOULD | Copy explicativa (tooltip/ajuda). |
| D7 | Biblioteca: evidència vinculada avisa abans d'eliminar | SHOULD | Confirmació amb el nombre de vincles. |

## 4. Criteri de tancament de Task 9 amb aquest addendum
Task 9 es tanca quan: (a) el fix round 1 de la revisió (I1–I2, M1–M6) és CLEAN; (b) el lot 9A (A1–A16) i el lot 9B (B1–B7, C1–C6, D1–D7) han passat revisió independent amb els criteris d'aquesta taula; (c) tot el que no s'ha pogut fer sense canvi d'arquitectura o sense dada oficial (p. ex. C2, C6) consta com a residual explícit a l'informe final. A17 i A18 es tanquen a Task 10.
