# Capa documental: plecs, originals i text per pàgina

Aquesta capa converteix una URL publicada per PLACSP en un original immutable i
un text navegable per pàgina. **No hi intervé cap IA ni cap OCR.**

## Cadena de traçabilitat

```
Tender
  └─ TenderDocument                    metadata i URL de PLACSP
       └─ TenderDocumentFile           versió: quan, d'on, quin resultat HTTP
            └─ TenderDocumentContent   SHA-256, bytes a S3
                 └─ TenderDocumentExtraction   parser_version, qualitat
                      └─ TenderDocumentPage    page_number 1-based, text
```

Donada qualsevol pàgina, tota la cadena es reconstrueix amb JOINs. Això és el
que permetrà que la fase següent pugui afirmar "solvència econòmica, PCAP
pàgina 27" i que sigui verificable.

## Numeració de pàgines

**1-based, a la base de dades i a la interfície.** La pàgina 27 desada és la
pàgina 27 que veu l'usuari. No hi ha cap capa de traducció: un off-by-one no es
podria colar sense que algú obrís el PDF i comptés.

## Deduplicació

El nivell de deduplicació **són els bytes**. El mateix SHA-256 produeix un sol
objecte a l'storage i una sola extracció per `parser_version`, apuntin-hi els
`TenderDocument` que apuntin.

El que **no** es dedupliqua és la petició HTTP: per conèixer el hash cal
baixar-los. Si el mateix PCAP surt a 40 expedients es fan 40 GET, però un sol
`PUT` i un sol parseig.

## Versionat

| Cas | Condició | Acció |
| --- | --- | --- |
| A | mateixa URL, mateix SHA-256 | Cap versió nova; s'actualitza `last_checked_at`. |
| B | mateixa URL, SHA-256 diferent | Versió nova; **l'original antic es conserva**. |
| C | URL diferent, SHA-256 conegut | Versió nova apuntant al contingut existent. |

Un `File` amb `download_status='failed'` no pren `is_current`: un error de
xarxa transitori no fa desaparèixer de la interfície un document ja processat.
Els números de versió poden tenir salts, perquè la fila que es reclama abans de
descarregar s'elimina quan resulta ser el cas A.

## Storage

```
tenders/documents/<sha256[0:2]>/<sha256[2:4]>/<sha256>.<ext>
```

Separat de `companies/` i de `sources/placsp/`. Tot l'accés passa per
`StorageService`; `storage_key` no apareix en cap resposta de l'API i l'original
es serveix per un endpoint autoritzat, no per MinIO.

## Parser

`pypdf`, `parser_version = "pypdf-v1"`. La llicència és el criteri decisiu:
PyMuPDF és AGPL-3.0 i, en un servei accessible per xarxa, obligaria a publicar
el codi del producte o a comprar llicència comercial.

Canviar de parser no destrueix res: una `parser_version` nova crea una extracció
nova sobre els mateixos bytes i marca l'anterior `is_current=false`.

## Qualitat i OCR

| Valor | Regla |
| --- | --- |
| `empty` | 0 caràcters. |
| `low_text` | Mitjana < `DOCUMENT_LOW_TEXT_CHARS_PER_PAGE` **o** > 50 % de pàgines buides. |
| `good` | La resta. |
| `failed` | L'extracció ha fallat. |
| `not_applicable` | El format no admet extracció de text. |

`ocr_candidate` és un **indici**, no una prova. Un plec pot ser legítimament
curt. **Aquesta iteració no fa OCR.**

## Seguretat: SSRF

`app/services/url_guard.py` valida, **abans de cada salt de redirecció**:
esquema `http`/`https`, absència de credencials a la URL, ports permesos, i
totes les IP resoltes contra loopback, privades, link-local, reservades,
multicast, CGNAT, ULA, IPv4 mapejades dins d'IPv6 i els endpoints de metadades
de cloud.

### Deute conegut

* **DNS rebinding.** Es valida la IP resolta, però `httpx` torna a resoldre en
  connectar. Tancar-ho vol dir connectar-se a la IP validada i sobreescriure la
  capçalera `Host`. Pendent.
* **Descàrrega síncrona.** El `POST` bloqueja fins que acaba. La lògica ja viu en
  un servei pur, així que moure-la a un worker no requerirà tocar-la.
* **Formats no-PDF.** DOCX, XLSX i ZIP es guarden però no se'n llegeix el text.

## Configuració

Tots els límits porten prefix `DOCUMENT_` a `app/core/config.py`. Vegeu
`.env.example`.
