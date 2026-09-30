"""Classificacio documental: que es cada document d'una licitacio.

Capa 3 del motor d'analisi. Treballa sobre els `TenderDocument` de la
licitacio, **vinguin del canal que vinguin**: no coneix la recepcio.

* `roles`: regles pures (rol, confianca, senyals i troballes del conjunt).
* `ooxml_text`: prou text d'un DOCX o XLSX per classificar-lo.

L'orquestracio i la persistencia son a `app/services/document_classification.py`.
"""
