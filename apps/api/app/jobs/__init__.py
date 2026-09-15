"""Feina asincrona persistent sobre PostgreSQL. Veure `app/models/jobs.py`.

* `params`    — parametres validats i identitat tecnica (`dedupe_key`) de cada feina.
* `queue`     — reclamar, renovar el lease, tancar, recollir abandonats i reconciliar.
* `requests`  — peticions dels tenants: crear-les, vincular-les i cancel·lar-les.
* `handlers`  — el que fa cada tipus de feina, reutilitzant els serveis existents.
* `worker`    — el proces que ho executa: `python -m app.jobs worker`.

Cap d'aquests moduls no depen de FastAPI.
"""
