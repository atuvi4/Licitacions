# Entrada de documents: recepció i classificació

> Fase 1 del [motor d'anàlisi](analysis-engine.md). Data: 2026-09-29.
> Cas gold: Ajuntament de l'Escala, *Telecomunicacions 2027*.

## 1. Quatre capes que no es coneixen

La primera entrada és la pujada manual d'un ZIP. Després n'hi haurà d'altres:
plataformes de contractació, Google Drive, APIs i altres connectors. Per això
l'anàlisi **no pot dependre de per on arriben els documents**. Hi ha quatre
capes, i cadascuna només coneix la de sota:

```
1. ORIGEN DE LA LICITACIÓ    Tender + Source (PLACSP, MANUAL...)            ja existia
        │
2. DOCUMENTS REBUTS          canal ─► Delivery ─► recepció ─► TenderDocument
   (app/reception)           zip_upload · (drive · platform · api: futurs)
        │                    Desa i registra. No classifica.
        ▼
3. CLASSIFICACIÓ DOCUMENTAL  TenderDocument(s) de la licitació ─► rols + troballes
   (app/classification)      No sap per quin canal van arribar.
        │                    Escriu `document_type`, que és l'única cosa que llegeix la capa 4.
        ▼
4. MOTOR D'ANÀLISI           requisits, compliment, etc.                     ja existia
                             Llegeix TenderDocument + document_type. No coneix ni 2 ni 3.
```

Regles que ho garanteixen:

* **Un canal nou és un adaptador nou, i res més.** Tradueix el que rep a una
  `Delivery` (`app/reception/delivery.py`): els fitxers acceptats amb els seus
  bytes, els omesos amb motiu i, si n'hi havia, el contenidor original. A partir
  d'aquí tots els canals passen pel mateix servei (`document_reception.receive`).
* **La recepció no classifica.** Desa els documents amb la mateixa porta que una
  pujada individual (`store_uploaded_document`) i demana la classificació de la
  licitació.
* **La classificació no coneix la recepció.** Treballa sobre els
  `TenderDocument` de la licitació, sigui quin sigui el camí pel qual hi van
  arribar (ZIP, pujada individual o, demà, Drive o una plataforma). L'únic que
  aprofita d'una recepció és el camí del fitxer dins de l'entrega (carpeta i
  accents), per ensenyar-lo i per classificar pel nom. I l'únic lligam a la base
  és informatiu (`trigger_reception_id`, `SET NULL`).
* **L'anàlisi no coneix ni l'una ni l'altra.** Continua triant els documents per
  `document_type`, com abans.

| Capa | Mòduls | Toca la BD? |
| --- | --- | --- |
| 2 · canal ZIP | `app/reception/archive.py` (obrir amb límits), `app/reception/zip_channel.py` (ZIP → `Delivery`) | no |
| 2 · contracte | `app/reception/delivery.py` | no |
| 2 · servei | `app/services/document_reception.py` | **sí** |
| 3 · regles | `app/classification/roles.py`, `app/classification/ooxml_text.py` | no |
| 3 · servei | `app/services/document_classification.py` | **sí** |
| API | `routers/receptions.py`, `routers/document_classification.py` | — |

## 2. Recepció (capa 2)

### Canal ZIP: obrir el paquet sense confiar-hi

Tot es fa **en memòria**: no s'escriu mai res al disc. Hi ha dues respostes
diferents, i no s'han de barrejar.

**El paquet es rebutja sencer** (`422 archive_rejected`, amb `details.reason`),
i no es desa res, ni el ZIP ni cap fitxer:

| `reason` | Quan |
| --- | --- |
| `not_a_zip` · `corrupt` | No és un ZIP o està malmès |
| `office_document` | És un DOCX/XLSX pujat com a paquet |
| `unsafe_path` | Camí absolut, unitat de Windows, `..` o caràcters de control |
| `symlink` · `encrypted` | Enllaç simbòlic o membre xifrat |
| `duplicate_entry` | Dues entrades amb el mateix nom |
| `compression_ratio` | Fitxer ≥ 1 MB amb una ràtio > `INTAKE_MAX_COMPRESSION_RATIO` (bomba) |
| `too_many_members` · `too_large_uncompressed` | Supera `INTAKE_MAX_MEMBERS` o `INTAKE_MAX_TOTAL_UNCOMPRESSED_MB` |

Els límits es comproven sobre les mides declarades, abans de descomprimir.
`zipfile` no retorna mai més bytes dels que diu la capçalera i en valida el CRC.
Els ZIP interns s'obren un sol nivell (queden `expanded`) i els seus fitxers
compten als mateixos límits. Els noms en UTF-8 sense la marca UTF-8 (típics
d'eines de Windows) es recuperen.

### Qualsevol canal: fitxers omesos

**Es registra un fitxer com a omès i la resta continua** (`skip_reason`):

| Motiu | `needs_attention` |
| --- | --- |
| `system_file` (`__MACOSX`, `.DS_Store`, `~$…`) | no (és soroll de l'eina) |
| `empty`, `too_large`, `unsupported_type`, `type_mismatch`, `nested_too_deep`, `unreadable_archive` | sí |

El tipus es valida contra el contracte de formats dels plecs
(`validate_tender_upload`). Si el canal no el declara (dins d'un ZIP no n'hi ha),
fa de declaració l'extensió, i els bytes hi manen igual.

### Documents normals i una sola revisió

Cada fitxer acceptat és un `TenderDocument` normal, amb el contingut deduplicat
per SHA-256, `origin = uploaded` i l'extracció de sempre. El contenidor original
també es desa, i `container_content_id` hi apunta amb `RESTRICT`. Una entrega
sencera és **una sola revisió** de la licitació.

### Errors a mig camí

* Si falla **desant**, la recepció queda `failed` i els documents que ja s'havien
  desat s'hi queden. Són plecs reals, i esborrar-los amagaria que l'entrega va
  arribar.
* Si falla **la classificació**, la recepció continua `completed`. La resposta
  diu `classification_failed: true` i la classificació es pot tornar a demanar.

## 3. Classificació (capa 3)

### Sempre el conjunt sencer

Cada passada classifica **tots** els documents de la licitació que tenen
contingut desat. Una còpia o un document que falta només es veuen mirant-ho tot.
Si el DOC-04 que faltava al ZIP arriba després per un altre camí, la passada
següent ja no el troba a faltar (hi ha un test que ho comprova).

Una passada (`tender_document_classification_runs`) és immutable, i només n'hi
ha una de vigent per (empresa, licitació). Porta una empremta dels documents
(id, contingut, camí, extracció i versió del classificador). Si l'empremta no ha
canviat, es reaprofita la passada vigent. Una passada neix en dos casos:

* `reception`: una entrega nova;
* `request`: algú la demana, per exemple després d'una pujada individual.

### Rols

Taxonomia tancada (`DocumentRole`): `pcap`, `ppt`, `characteristics_table`,
`offer_model`, `inventory_annex`, `justification_report`,
`approval_resolution`, `administrative_form`, `notice` i `other`.

Cada rol porta els **senyals** que el sostenen (`signals`):

1. **Títol de la primera pàgina.** Es busca en els primers 1.500 caràcters
   normalitzats, i mana el patró que apareix **abans**: la primera pàgina d'un
   PCAP sovint cita el PPT. Si surt en els primers 600 caràcters, la confiança
   és alta; si no, mitjana.
2. **Nom del fitxer.** Sol, mai no dona confiança alta: mitjana si el text no el
   contradiu, i baixa si no hi ha text.
3. **Nom i títol coincideixen.** Una confiança mitjana puja a alta.
4. **Fulls de càlcul.** Es classifiquen pels rètols. Confiança màxima: mitjana.
5. **Codi intern.** Es llegeix només del bloc de control (`Codi DOC-03`).

**Mana el contingut, no el nom.** Si discrepen, el rol és el del contingut i hi
ha una troballa. Un «PCAP» que comença pel quadre de característiques no és una
discrepància.

### Què arriba a l'anàlisi

La capa 3 escriu el `document_type`, que és el que llegeix la capa 4:

* **Només si és segur.** El `document_type` surt del rol només si la confiança
  és alta i el document no és una còpia. Si no, `other`: l'anàlisi no el tria
  fins que una persona el fixa.
* **Només en licitacions de l'empresa** (obertes a mà i seves). En una licitació
  pública aquest camp és de tothom.
* **Només en documents que gestiona**: els que són `other`, els que tenen el
  tipus que els va donar la passada anterior i els que tenen un rol fixat per una
  persona. Un tipus que algú va triar en pujar un plec sol no el trepitja cap
  reclassificació.

| Rol | `document_type` |
| --- | --- |
| `pcap`, `characteristics_table` | `administrative_terms` |
| `ppt` | `technical_terms` |
| `offer_model`, `inventory_annex` | `additional` |
| `notice` | `notice` |
| la resta | `other` |

### Troballes

| Codi | Gravetat | Què vol dir |
| --- | --- | --- |
| `title_content_mismatch` | alta/mitjana | El nom diu una cosa i el títol una altra |
| `duplicate_text` | alta | Dos fitxers diferents amb el mateix text: un no és el que diu. Es conserva el coherent. |
| `duplicate_bytes` | mitjana | El mateix fitxer dues vegades |
| `duplicate_internal_code` | mitjana | Dos textos diferents declaren el mateix codi (possibles versions) |
| `referenced_document_missing` | alta | Es cita un codi (`DOC-04`) que cap document declara. Porta la pàgina i l'extracte de cada cita. Només s'aplica si la licitació fa servir codis. |
| `required_role_missing` | alta | Falta PCAP o PPT |
| `no_text` | mitjana | PDF sense text (escanejat): no s'inventa cap OCR |
| `format_not_analyzable` | baixa | DOCX/XLSX: es classifica, però l'anàlisi encara no el llegeix |
| `role_undetermined` | baixa | Cap senyal de rol |

`needs_review` és cert quan ningú no ha fixat el rol **i** la proposta no és de
confiança alta o hi ha una troballa alta o mitjana sobre el document.

### La persona decideix

`PUT …/document-classification/documents/{document_id}/role` fixa el rol:

* la decisió (`tender_document_role_decisions`) penja **del document, no de la
  passada**, i per això sobreviu a les reclassificacions;
* el document passa al `document_type` del rol, encara que el classificador
  l'hagués deixat fora: qui ho fixa en respon;
* el que va proposar el classificador no es toca mai, ni les troballes. Sense
  això no es podria mesurar mai si el classificador encertava.

## 4. API

Totes les rutes pengen de `/companies/{company_id}/tenders/{tender_id}`.

| Ruta | Qui |
| --- | --- |
| `POST /receptions/zip` (multipart `file`) | Rols que editen, i només en una licitació oberta a mà i de l'empresa. Retorna l'entrega i la classificació resultant. |
| `GET /receptions`, `GET /receptions/{id}` | Qualsevol membre que vegi la licitació |
| `GET /document-classification` | Ídem |
| `POST /document-classification` | Rols que editen, en licitacions de l'empresa. Torna a classificar; si res no ha canviat, no fa res. |
| `PUT /document-classification/documents/{document_id}/role` | Ídem. Fixa el rol. |

Cada canal futur tindrà la seva ruta a `/receptions/…` i tornarà el mateix. Una
licitació o una entrega d'una altra empresa donen `404`. L'auditoria només porta
identificadors, codis i xifres: mai noms de fitxer ni text del plec.

## 5. Model

Sis taules de tenant (migració `0044_reception_classification`). Tots els enums
porten CHECK, i les filles referencien el pare per `(id, company_id)`.

| Capa | Taula | Contingut |
| --- | --- | --- |
| 2 | `tender_document_receptions` | Canal, etiqueta, referència a l'origen, contenidor original, estat i comptadors |
| 2 | `tender_document_reception_items` | Camí, mida, SHA-256, estat, `skip_reason`, referència a l'origen i document creat |
| 3 | `tender_document_classification_runs` | Versió, empremta, disparador (i entrega, si n'hi ha), vigent |
| 3 | `tender_document_role_suggestions` | Rol, confiança, `document_type` proposat i senyals, per document |
| 3 | `tender_document_classification_findings` | Codi, gravetat, document, document relacionat i detall |
| 3 | `tender_document_role_decisions` | Rol fixat per una persona (un per empresa i document) |

Cadena: `… → 0042_user_password_change → 0043_company_platform_status →
0044_reception_classification`.

## 6. Gold: l'Escala

`datasets/classification/lescala-2026.gold.json` descriu **documents i
troballes**, no l'entrega. Per a cadascun dels 8 documents hi ha el rol, la
confiança, el `document_type`, els senyals i el SHA-256. També hi ha les 5
troballes esperades:

* el `04_Annex_Mestre_Inventaris…` porta el text del DOC-03: un títol que
  enganya i una còpia del 03;
* el DOC-04 es cita al PPT (p. 1) i no hi és;
* el XLSX i el DOCX es classifiquen, però no s'analitzen.

Hi ha dos tests contra el mateix gold:

* `test_classification_gold_lescala.py` fa servir els **documents reals**. No són
  al repositori: els busca a `CLASSIFICATION_GOLD_LESCALA_ZIP` o a
  `var/gold/lescala-2026.zip`, i se salta si no hi són. Comprova el SHA-256 de
  cada fitxer.
* `test_document_classification_api.py::test_the_lescala_twin_matches_the_gold`
  fa servir el **bessó sintètic** (`tests/fixtures/tender_packages.py`). Corre
  sempre.

## 7. Com s'afegeix un canal

1. Un enum nou a `ReceptionChannel`, i el seu valor al CHECK en una migració.
2. Un adaptador que produeixi una `Delivery`. Si l'origen té identificadors
   (id de Drive, URL…), van a `reference` i `source_reference`.
3. Una ruta a `/receptions/<canal>` que cridi `document_reception.receive`.

Cap canvi a la classificació ni a l'anàlisi. Si un canal porta un contenidor
(un altre ZIP, per exemple), reutilitza `archive.scan_archive`.

## 8. Límits coneguts

* **DOCX i XLSX no s'analitzen.** Se'n llegeix el text només per classificar-los.
  L'extracció citable d'Excel (fulls, cel·les i fórmules) és una peça pendent.
* **Regles de títol en català i castellà.**
* **Els codis interns** només es reconeixen amb el format `DOC-NN`.
* **Síncron.** Una entrega es processa dins de la petició. Amb entregues grans o
  canals lents (Drive, plataformes) caldrà passar-la a un job; el servei ja no
  depèn d'HTTP.
* **Classificar en licitacions públiques** no està obert: la passada i les
  decisions només s'hi escriuen en licitacions de l'empresa.
* **Sense interfície.** Només hi ha API.
