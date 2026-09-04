# Fixtures de PLACSP

## Procedència

Aquests fitxers **no són inventats**. Surten del feed oficial de sindicació de
la Plataforma de Contratación del Sector Público:

```
https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom
```

descarregat el **2026-09-03** (`feed/updated` = `2026-09-02T20:14:33+02:00`,
176 entrades). D'aquell fitxer se n'han extret unes poques entrades reals,
conservant l'XML tal com el publica la font: mateixos namespaces, mateixos
codis i mateixa estructura.

Són dades **públiques** publicades en obert per l'Administració. No contenen
cap dada d'un tenant ni cap dada personal més enllà del contacte oficial de
l'òrgan de contractació, que ja és públic.

## Fitxers

| Fitxer | Què cobreix |
| --- | --- |
| `feed_sample.atom` | Tres expedients reals: un **sense lots**, un amb **3 lots** i un amb **7 lots i 22 CPV**. Imports, terminis, organismes, NUTS i documents. |
| `feed_sample_modified.atom` | El mateix contingut, amb el **termini del primer expedient canviat** (2026-07-30 → 2027-01-31) i `updated` posterior. Serveix per provar revisions: mateix `external_id`, hash diferent. |
| `feed_minimal.atom` | Expedient amb **només els camps obligatoris**: sense imports, sense CPV, sense NUTS i sense termini. Ha de normalitzar-se amb nulls, no petar. |
| `feed_malformed.atom` | Una entrada bona, una amb **dades brutes** (import no numèric, data 31 de febrer, codis inexistents) i una **sense `<id>`**. La bona s'ha de processar igualment. |
| `feed_with_tombstone.atom` | Una entrada bona i un `at:deleted-entry`, que és com la font marca un expedient anul·lat. |

## Com actualitzar-los

```bash
curl -A "LicitacionsBot/0.1" -o feed_real.atom \
  https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom
```

i tornar a retallar-ne les entrades. Val la pena refrescar-los si l'especificació
canvia de versió: l'actual és la **1.10 (17/09/2024)**.

Els expedients continguts ara mateix:

| entry id | expedient | lots | CPV |
| --- | --- | --- | --- |
| `…/20232857` | AMARCO/2026/MUSEO/607BAS | 0 | 1 |
| `…/20050278` | 26-002735 | 3 | 1 |
| `…/20233354` | 2026-044-O | 7 | 22 |
