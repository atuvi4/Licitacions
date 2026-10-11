# Fixture d'un tender públic sintètic (3B-2 §10, Task 11)

## Procedència

**Tot és inventat.** A diferència de `tests/fixtures/placsp/`, cap fitxer d'aquí surt
de cap feed real: l'expedient, l'òrgan («Ajuntament de Vilaquimera»), els imports,
les URL i el text dels dos documents són sintètics. No hi ha cap NIF, cap URL real
(tots els hosts són `*.example.test`, un TLD reservat que no resol mai), cap nom
d'empresa real ni cap frase d'un plec real.

Serveix per recórrer el camí públic sencer sense xarxa ni cap model (`fake`): ingesta
per fixture (font `placsp_agregadas`, `owner_company_id NULL`), processat dels plecs
amb el fake de descàrrega dels tests, classificació global, quota bloquejada per
`provider_not_real`, workspace i historial. És el mateix camí que seguirà L'Escala al
pilot (spec §8 fins al pas 5 i bloqueig honest al pas 6).

## Fitxers

| Fitxer | Què és |
| --- | --- |
| `feed.atom` | Una sola entrada PLACSP-like (mateixos namespaces i estructura que el feed agregat 1044) amb un `LegalDocumentReference` (PCAP) i un `TechnicalDocumentReference` (PPT). |
| `pcap-p1.txt`, `pcap-p2.txt` | Les dues pàgines de text del PCAP sintètic (es declara `DOC-01`). |
| `ppt-p1.txt`, `ppt-p2.txt` | Les dues pàgines de text del PPT sintètic (es declara `DOC-02`). |
| `__init__.py` | Accés: URL dels documents, PDF generats amb `pdf_builder` a partir dels `.txt`, SHA-256, client fals de descàrrega i resolutor DNS. |

Els PDF no es desen al repositori: es generen de manera determinista a partir dels
`.txt` (`build_pdf`), de manera que el diff del text és llegible i el SHA-256 és
estable.

## Què NO conté a propòsit

* cap referència a un `DOC-NN` que no sigui el del mateix document: el paquet és
  **complet** (cap `referenced_document_missing`, cap forat);
* cap lot, cap anunci d'adjudicació, cap document de resultat;
* cap termini vençut: el termini és el 2027-01-15.
