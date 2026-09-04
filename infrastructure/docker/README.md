# infrastructure/docker

Els `Dockerfile` de cada aplicacio viuen al costat del seu codi
(`apps/api/Dockerfile`, `apps/web/Dockerfile`) perque el context de build sigui
minim i el cache d'npm/pip funcioni be.

Aquest directori queda reservat per a la infraestructura que encara no existeix:

* configuracio de reverse proxy (nginx / Traefik) per a desplegaments,
* `docker-compose.prod.yml` amb imatges construides i sense volums de codi,
* fitxers d'inicialitzacio de PostgreSQL (extensions com `pgvector` quan calgui),
* definicio dels workers (Celery/RQ) quan s'implementin.

Res d'aixo forma part de la iteracio 1.
