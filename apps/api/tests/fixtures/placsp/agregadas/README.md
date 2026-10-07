# Fixtures del feed agregat 1044 (PLACSP, plataformes agregades)

## Procedència

Entrades reals del feed oficial de sindicació agregada:

```
https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores.atom
```

Capturat el **2026-10-06** (una sola pàgina, 2,7 MB; `feed/updated` =
`2026-10-06T03:00:35.423+02:00`, 226 entrades i 76 làpides; no s'ha seguit `next`
ni s'ha baixat cap document). XML conservat tal com el publica la font (mateixos
namespaces i codis) llevat dels retalls i variants descrits a sota.

**Sense dades personals.** S'ha eliminat el bloc `author` de la capçalera (adreça
de correu institucional) i es filtren `cac:Contact`, `cbc:Telephone`,
`cbc:Telefax` i `cbc:ElectronicMail` (la pàgina capturada no en duia cap a les
entrades triades). Només queden dades públiques de licitacions i d'òrgans.
`tests/test_ingestion_agregadas_parser.py` ho comprova.

## Retalls

* Només entrades de `contractaciopublica.cat`. `entry_lots.atom` en té 4: la
  primera (expedient `CTON2026000028`, 2 lots + 2 documents, `id` `…/20529505`),
  una sense lots, una amb `TED` i una amb 2 lots.
* `ID_OC_PLAT` és l'únic identificador de l'òrgan (cap `ID_PLATAFORMA`/DIR3/NIF).

## Sintètic (a més del retall)

* **NIF d'adjudicatària emmascarat**: el NIF real de l'empresa adjudicatària
  (`cac:WinningParty`, a `entry_lots.atom` i `page1.atom`) s'ha substituït pel
  sintètic `B00000000`. Cap dada real d'empreses a les fixtures, encara que la
  font la publiqui.
* **CPV de lot**: la pàgina capturada no porta cap CPV a nivell de lot; se n'ha
  afegit un (`45233222`, copiat del CPV principal) al lot 1 de la primera entrada.
* **`tombstone_anulada.atom`**: la pàgina només té làpides `CERRADA`; aquesta és
  una làpida real amb `type` canviat a `ANULADA`.
* **`tombstone_old.atom`**: làpida real amb `ref` apuntant a l'entrada de
  `entry_lots.atom` i `when` = `2026-10-05T10:00:00+02:00` (anterior al seu `updated`).
* **`page2.atom`**: entrades reals amb `updated` reescrit a 2026-10-02 per tenir una
  pàgina >= 3 dies més antiga que la 1 (prova del solapament de 48 h).
* Les làpides `tombstone_cerrada` i `tombstone_anulada` mantenen el `at:comment`
  real, però tenen `ref` reescrit a l'`atom:id` de l'entrada de `entry_lots.atom`
  i `when` = `updated` de l'entrada + 1 dia (2026-10-06T20:00:34.510+02:00).
  Ordre: `tombstone_old` < `updated` de l'entrada < làpides < `entry_lots_republished`.

## Fitxers

| Fitxer | Què cobreix |
| --- | --- |
| `entry_lots.atom` | 4 entrades (primera: 2 lots, CPV de lot sintètic, 2 documents; una amb TED). Base de les variants. |
| `entry_lots_older.atom` | Mateixa entrada, `updated` −1 dia (2026-10-04) i termini antic (2026-10-30). |
| `entry_lots_same_updated_other_hash.atom` | Mateix `updated`, títol (atom i `ProcurementProject/Name`) amb «(correcció)». |
| `entry_lots_doc_removed.atom` | Sense el `TechnicalDocumentReference` (PPT); `updated` +1 dia (2026-10-06T20:00). |
| `entry_lots_lot_removed.atom` | Sense el lot 2; `updated` +1 dia. |
| `entry_lots_republished.atom` | Entrada completa; `updated` +2 dies (2026-10-07T20:00). |
| `tombstone_cerrada.atom` / `tombstone_anulada.atom` | Una làpida cadascuna (`CERRADA` real; `ANULADA` sintètica). |
| `tombstone_old.atom` | Làpida de l'entrada base amb `when` anterior al seu `updated`. |
| `page1.atom` / `page2.atom` | Pàgina 1 (2 entrades de 2026-10-05 + 1 làpida, `next` → pàgina 2); pàgina 2 (2 entrades de 2026-10-02, sense `next`). |
