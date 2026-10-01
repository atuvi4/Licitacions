"""Motor d'analisi d'un plec contra el coneixement actual d'una empresa (0051).

Tot el que hi ha aqui es PUR: dataclasses d'entrada, funcions deterministes i
cap sessio. La capa que llegeix i escriu la base es `app.services.tender_analysis`.

Capes que NO es barregen: (1) intel·ligencia de fabricant, (2) coneixement de
l'empresa, (3) requisits del plec, (4) solucio per al plec. Aquest motor compara
(2) amb (3) i escriu nomes resultats derivats; no crea cap solucio, cap producte,
cap preu ni cap recomanacio de presentar-se.
"""
