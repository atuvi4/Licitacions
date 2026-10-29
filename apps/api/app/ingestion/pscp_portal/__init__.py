"""Connector del portal de la PSCP (`contractaciopublica.cat/portal-api`, Bloc 2, etapa 2).

El feed de PLACSP nomes enumera part del paquet d'una licitacio publicada a la PSCP (sovint
PCAP i PPT); la resta (memoria, aprovacio, annexos, preguntes i respostes oficials, avisos
del tauler i esmenes) nomes es al portal. Aquest connector els llegeix i els governa com a
origen `portal` (0065): baixats pel sistema i verificats a l'origen amb l'MD5 que publica el
portal, sense cap verificacio manual.

Moduls:

* `client`: GET a l'API publica del portal (nomes `PORTAL_API`), amb User-Agent identificable,
  pausa entre peticions, reintents limitats davant de 429 i 5xx, limit dur de peticions i
  registre de cada peticio. Apagat (`PSCP_PORTAL_ENABLED`), no obre cap connexio.
* `parser`: lectura pura de les respostes (publicacio, fases, esmenes, tauler, llistes,
  preguntes i respostes, avisos, adjunts) i de les URL de la font i de descarrega.
* `render`: els documents de text que el portal no publica com a fitxer, deterministes:
  «Preguntes i respostes (PSCP)» (`pscp-qa-v1`) i els avisos i esmenes (`pscp-avis-v1`).
* `service`: la sincronitzacio d'una licitacio (lectura completa, reconciliacio, cessio dels
  `operator_url` amb l'MD5 comprovat, descarregues i revisions). La crida la feina
  `sync_portal_documents` del worker.

Prioritat entre origens: feed > portal > operador. Una URL que el feed publica no la toca el
portal (i, si el portal la governava, el feed l'adopta); una URL que el feed ha deixat de
publicar i el portal encara publica passa al portal. Els mateixos bytes sota dues URL queden
com a alies (`document_alias.ORIGIN_PRIORITY`): el contingut es reaprofita pel SHA-256, mai es
duplica.
"""

from __future__ import annotations

PORTAL_HOST = "contractaciopublica.cat"
PORTAL = f"https://{PORTAL_HOST}"
#: L'API publica (GET sense autenticacio) d'on surt tot el que llegeix el connector.
PORTAL_API = f"{PORTAL}/portal-api"

#: `Source.code` de la font que governa els documents `portal`.
SOURCE_CODE = "pscp_portal"
SOURCE_NAME = "Portal de la PSCP (contractaciopublica.cat)"

#: La fase «Anunci de licitacio»: d'aqui surten els plecs i annexos que es llegeixen.
LICITACIO_PHASE = 1000040

#: Versio del renderitzador de les preguntes i respostes. Si canvia el format, canvia aixo.
QA_RENDERER = "pscp-qa-v1"
#: Versio del renderitzador dels avisos del tauler i de les esmenes de la publicacio.
NOTICE_RENDERER = "pscp-avis-v1"

#: Esquema de les URL sintetiques dels documents generats (no es descarreguen mai).
GENERATED_SCHEME = "pscp-portal://"

#: Disparador de la revisio que deixa la reconciliacio: el publica l'organ al seu perfil,
#: per tant l'historial el llegeix com a `source` (`tenders.changes.REVISION_ORIGINS`).
PORTAL_SYNC_TRIGGER = "portal_sync"
