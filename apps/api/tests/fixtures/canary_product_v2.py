"""Paquet SINTETIC del canary `product-v2` (Attempt 3): una licitacio inventada.

Cap dada real (ni d'una empresa ni de cap plec): Vilasintetica, l'expedient i els imports
son ficticis. Es el mateix text que el fixture del canary (v2, catala correcte). L'oracle
NO es aquest text: els tests el deriven del que en treu el parser de producte
(`extract_pdf_text`), que es el que veu el model i contra el que es validen les cites.

Gold:
* p2: ISO 9001 obligatori; garantia definitiva del 5 %.
* p3: atencio d'avaries critiques en 4 hores; asseguranca de RC de 600.000 euros.
* p4: criteris d'adjudicacio (preu 60 punts; millora del termini 25; pla 15).
* p5: clausula material deliberadament indeterminada (classificacio empresarial).
* p7: presentacio obligatoria en sobre digital.
"""

from __future__ import annotations

_FILL = (
    "Aquest paràgraf és text de farciment del document sintètic i no conté cap obligació. "
    "Serveix perquè la pàgina tingui una densitat de text semblant a la d'un plec real. "
)

PCAP_PAGES = [
    "PLEC DE CLÀUSULES ADMINISTRATIVES PARTICULARS (SINTÈTIC)\n"
    "Ajuntament de Vilasintètica. Expedient CANARY-0001.\n"
    "Clàusula 1. Objecte. L'objecte del contracte és el servei de manteniment preventiu i "
    "correctiu de l'enllumenat públic del municipi de Vilasintètica.\n"
    "Clàusula 2. Durada. El contracte tindrà una durada de 24 mesos, prorrogable 12 mesos més.\n"
    + _FILL
    * 3,
    "Clàusula 3. Solvència tècnica.\n"
    "El licitador ha d'acreditar obligatòriament un certificat ISO 9001 vigent, emès per una "
    "entitat acreditada, que inclogui el manteniment d'instal·lacions d'enllumenat.\n"
    "Clàusula 4. Garantia. L'adjudicatari ha de constituir una garantia definitiva del 5 % "
    "de l'import d'adjudicació, IVA exclòs.\n" + _FILL * 3,
    "Clàusula 5. Condicions d'execució.\n"
    "El contractista ha d'atendre les avaries crítiques en un termini màxim de 4 hores des de "
    "l'avís municipal, les 24 hores del dia i els 365 dies de l'any.\n"
    "Clàusula 6. Assegurança. El contractista ha de disposar d'una assegurança de "
    "responsabilitat civil amb una cobertura mínima de 600.000 euros per sinistre durant "
    "tota la durada del contracte.\n" + _FILL * 3,
    "Clàusula 7. Criteris d'adjudicació (100 punts).\n"
    "a) Preu: fins a 60 punts. Puntuació = 60 x (oferta més baixa / oferta valorada).\n"
    "b) Millora del termini de resposta a avaries crítiques: fins a 25 punts; 25 punts per a "
    "un termini de 2 hores o menys, 10 punts per a 3 hores i 0 punts per a 4 hores.\n"
    "c) Pla de manteniment preventiu: fins a 15 punts, segons judici de valor.\n" + _FILL * 3,
    "Clàusula 8. Classificació empresarial.\n"
    "La classificació empresarial exigible, si escau, es determinarà més endavant i es "
    "comunicarà als licitadors. Mentre no es comuniqui, no s'especifica cap grup ni subgrup.\n"
    + _FILL
    * 4,
    "Clàusula 9. Penalitats.\n"
    "Per cada hora de retard en l'atenció d'una avaria crítica, s'imposarà una penalitat de "
    "150 euros, amb un màxim del 10 % del preu del contracte.\n" + _FILL * 4,
    "Clàusula 10. Presentació d'ofertes.\n"
    "Les ofertes s'han de presentar obligatòriament en format electrònic, en un únic sobre "
    "digital, abans de la data límit que fixi l'anunci de licitació.\n" + _FILL * 4,
]

#: (fet, pagina, una paraula que identifica la linia de l'oracle).
MANDATORY = (
    ("p2_iso9001", 2, "ISO 9001"),
    ("p2_garantia_5", 2, "garantia definitiva"),
    ("p3_termini_4h", 3, "4 hores des de"),
    ("p3_rc_600000", 3, "600.000"),
    ("p7_sobre_digital", 7, "sobre"),
)
CRITERION = ("c_preu_60", 4, "60 punts")
INDETERMINATE = ("p5_classificacio", 5, "determinarà")


def oracle_sentence(page_text: str, marker: str) -> str:
    """La frase que conte `marker`, tal com la deixa el parser (fins al punt final)."""
    for line in page_text.split("\n"):
        if marker in line:
            for sentence in line.split(". "):
                if marker in sentence:
                    return sentence if sentence.endswith(".") else sentence + "."
    raise AssertionError(f"{marker!r} no es al text extret")
