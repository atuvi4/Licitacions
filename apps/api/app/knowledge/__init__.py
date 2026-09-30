"""Coneixement de l'empresa (fase 2 del motor d'analisi).

Tres peces:

* `vocabulary`: els vocabularis GLOBALS (linies de servei, capacitats i normes
  de certificacio). Tancats: nomes els canvia una migracio, mai un usuari ni un
  model de llenguatge en temps d'execucio.
* `snapshot`: la fotografia efectiva del coneixement d'una empresa, amb la
  precedencia resolta (actual verificat > actual declarat > historic >
  desconegut) i les avaluacions contra un requisit. Pura.
* `proposals`: la forma de les propostes que porta una observacio historica per
  poder-se promoure, manualment, a coneixement actual.
"""
