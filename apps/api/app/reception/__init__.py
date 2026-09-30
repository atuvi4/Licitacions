"""Recepcio de documents: com arriben els plecs d'una licitacio.

Capa 2 del motor d'analisi (1: origen de la licitacio; 2: documents rebuts;
3: classificacio documental; 4: analisi).

Cada canal (avui el ZIP pujat a ma; dema Google Drive, plataformes de
contractacio, API...) es un **adaptador** que tradueix el que rep a una
`Delivery` (`delivery.py`). A partir d'aqui tot es igual per a tots els canals:
el servei `document_reception` desa els documents i demana la classificacio, i
cap capa posterior no sap per quin canal van arribar.
"""
