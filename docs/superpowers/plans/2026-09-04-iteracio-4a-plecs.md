# Iteració 4A — Descàrrega de plecs i extracció de text per pàgina — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Descarregar documents públics d'una licitació, conservar-ne l'original immutable identificat per SHA-256, i extreure'n el text preservant la separació per pàgina, amb traçabilitat completa i sense cap IA.

**Architecture:** Quatre taules globals noves (sense `company_id`): `TenderDocumentContent` (blob adreçat pel hash), `TenderDocumentExtraction` (una passada de parser sobre un blob), `TenderDocumentPage` (text 1-based) i `TenderDocumentFile` (versió que lliga un `TenderDocument` amb un `Content`). El processat és síncron, protegit per un índex únic parcial, i la lògica viu en serveis purs que no saben res d'HTTP.

**Tech Stack:** FastAPI · SQLAlchemy 2 asyncio · Alembic · httpx · pypdf (BSD-3-Clause) · PostgreSQL 16 · MinIO (S3) · Next.js 15 · pytest amb SQLite en memòria.

**Spec:** `docs/superpowers/specs/2026-09-04-plecs-descarrega-extraccio-design.md`

## Global Constraints

- **Cap IA, cap OCR, cap embedding.** Ni Claude, ni OpenAI, ni pgvector, ni RAG, ni extracció de requisits. Si un pas et sembla que ho demana, no ho fa: atura't i pregunta.
- **Dependència nova única: `pypdf>=5.1`.** Cap altra. No afegeixis `reportlab`, `pdfminer.six`, `PyMuPDF`, `python-magic` ni `requests`.
- **Cap taula nova porta `company_id`.** Els plecs són públics i globals.
- **Cap `boto3` fora de `app/services/storage.py`.** Tot l'accés a binaris passa per `StorageService`.
- **Cap `storage_key` en cap schema de resposta de l'API.**
- **`PARSER_VERSION = "pypdf-v1"`**, definit un sol cop a `app/services/pdf_text.py`.
- **Numeració de pàgines 1-based** a la base de dades i a la UI. Mai 0-based enlloc.
- **Cap número màgic.** Tot límit viu a `app/core/config.py` amb prefix `DOCUMENT_`.
- **Cap test depèn d'Internet.** Un únic test aixeca un servidor HTTP a `127.0.0.1`.
- **Mai es loguega el text dels plecs**, ni la URL completa: només el host.
- **No es descarreguen els 1.454 documents.** Les proves reals toquen 3-5 tenders.
- Comentaris i docstrings en **català sense accents** (convenció del codi existent); els fitxers `.md` i els textos de UI sí que porten accents.
- La suite corre sobre **SQLite en memòria**; qualsevol índex parcial necessita `sqlite_where` **i** `postgresql_where`.
- Els tests es llancen des de `apps/api` amb `python -m pytest`.

---

## File Structure

**Crear (backend)**

| Fitxer | Responsabilitat |
| --- | --- |
| `app/core/http.py` | Identitat HTTP compartida: `user_agent()`. |
| `app/services/url_guard.py` | Validació SSRF d'una URL abans de connectar. No fa xarxa més enllà del DNS. |
| `app/services/document_download.py` | Descàrrega streaming, reintents, redireccions validades, sniffing i hash. No toca la BD. |
| `app/services/pdf_text.py` | `pypdf` → pàgines normalitzades + qualitat determinista. Funció pura sobre `bytes`. |
| `app/services/tender_document_processing.py` | Orquestració: claim, dedup, versionat, storage, transaccions. L'únic que toca la BD. |
| `app/models/tender_documents.py` | Les quatre entitats noves. |
| `app/schemas/tender_documents.py` | Schemes Pydantic de sortida. |
| `app/api/v1/routers/tender_documents.py` | Els vuit endpoints. |
| `alembic/versions/0005_tender_document_files.py` | Migració, només taules noves. |
| `tests/fixtures/pdf_builder.py` | Generador de PDF mínims per sintaxi crua. |
| `scripts/process_real_documents.py` | Prova real controlada sobre PLACSP. |
| `docs/documents.md` | Documentació de la capa documental. |

**Modificar (backend)**

| Fitxer | Canvi |
| --- | --- |
| `app/core/config.py` | 13 paràmetres `DOCUMENT_*`, `INGESTION_CONTACT_EMAIL`, 4 propietats derivades, 1 validator. |
| `app/core/errors.py` | `ProcessingInProgressError`, `NoContentToExtractError`. |
| `app/models/enums.py` | `DownloadStatus`, `ExtractionStatus`, `ExtractionQuality`, `PageExtractionStatus`. |
| `app/models/__init__.py` | Registre de les quatre entitats noves. |
| `app/services/storage.py` | `build_tender_document_key()`. |
| `app/ingestion/placsp/client.py` | `user_agent()` passa a importar-se de `app/core/http.py`. |
| `app/schemas/tenders.py` | `TenderDocumentFileSummary` + camp `latest_file` a `TenderDocumentRead`. |
| `app/api/v1/routers/tenders.py` | La fitxa carrega el resum del fitxer actual de cada document. |
| `app/api/v1/router.py` | Registre del router nou. |
| `pyproject.toml` | `pypdf>=5.1`. |

**Frontend**

| Fitxer | Canvi |
| --- | --- |
| `apps/web/src/lib/tenders.ts` | Tipus i etiquetes de la capa documental. |
| `apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx` | Secció Documents amb estat, `[Processa]` i selecció múltiple. |
| `apps/web/src/app/(app)/licitacions/[tenderId]/documents/[documentId]/page.tsx` | Vista de document amb navegador de pàgines. |

**Ordre de dependències:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → (9, 10) → 11 → 12.

---

## Task 1: Configuració, enums, clau d'storage i identitat HTTP

Els fonaments que la resta de tasques importen. No hi ha lògica de negoci: només valors centralitzats i tres funcions petites.

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/app/core/http.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/errors.py`
- Modify: `apps/api/app/models/enums.py`
- Modify: `apps/api/app/services/storage.py`
- Modify: `apps/api/app/ingestion/placsp/client.py`
- Test: `apps/api/tests/test_config_and_storage.py`

**Interfaces:**
- Produces:
  - `app.core.http.user_agent() -> str`
  - `settings.DOCUMENT_*` (13 camps) i les propietats `settings.document_max_size_bytes -> int`, `settings.document_allowed_mime_types -> set[str]`, `settings.document_text_extraction_mime_types -> set[str]`, `settings.document_allowed_ports -> set[int]`
  - `app.models.enums.DownloadStatus`, `ExtractionStatus`, `ExtractionQuality`, `PageExtractionStatus`
  - `app.services.storage.build_tender_document_key(sha256: str, extension: str) -> str`
  - `app.core.errors.ProcessingInProgressError`, `app.core.errors.NoContentToExtractError`

- [ ] **Step 1: Afegeix la dependència `pypdf`**

A `apps/api/pyproject.toml`, dins de `[project].dependencies`, just després de la línia de `defusedxml`:

```toml
    # Extraccio de text de PDF. BSD-3-Clause: a diferencia de PyMuPDF (AGPL),
    # no obliga a publicar el codi d'un servei SaaS. Python pur, sense
    # dependencies de sistema.
    "pypdf>=5.1",
```

- [ ] **Step 2: Instal·la-la**

Run: `cd apps/api && python -m pip install -e ".[dev]"`
Expected: instal·la `pypdf` sense errors.

- [ ] **Step 3: Escriu els tests que fallen**

Afegeix al final de `apps/api/tests/test_config_and_storage.py`:

```python
def test_build_tender_document_key_is_content_addressed():
    from app.services.storage import build_tender_document_key

    sha = "a" * 64
    key = build_tender_document_key(sha, "pdf")

    assert key == f"tenders/documents/aa/aa/{sha}.pdf"
    # Ha de quedar fora de l'espai de noms de les empreses i de la ingesta.
    assert not key.startswith("companies/")
    assert not key.startswith("sources/")


def test_build_tender_document_key_validates_the_hash():
    from app.services.storage import StorageError, build_tender_document_key

    with pytest.raises(StorageError):
        build_tender_document_key("no-es-un-hash", "pdf")


def test_build_tender_document_key_sanitises_the_extension():
    from app.services.storage import build_tender_document_key

    sha = "b" * 64
    assert build_tender_document_key(sha, "../evil").endswith(f"{sha}.evil")
    assert build_tender_document_key(sha, "") == f"tenders/documents/bb/bb/{sha}"


def test_generated_tender_document_key_passes_the_key_validator():
    from app.services.storage import build_tender_document_key, validate_storage_key

    key = build_tender_document_key("c" * 64, "pdf")
    assert validate_storage_key(key) == key


def test_document_settings_expose_derived_limits():
    from app.core.config import settings

    assert settings.document_max_size_bytes == settings.DOCUMENT_MAX_SIZE_MB * 1024 * 1024
    assert "application/pdf" in settings.document_allowed_mime_types
    assert settings.document_text_extraction_mime_types == {"application/pdf"}
    assert settings.document_allowed_ports == {80, 443}


def test_private_host_downloads_are_refused_in_production():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="x" * 48,
            DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS=True,
        )


def test_user_agent_identifies_the_bot_and_the_contact():
    from app.core.http import user_agent

    agent = user_agent()
    assert agent.startswith("LicitacionsBot/")
    assert "contacte" in agent
```

Comprova que `import pytest` ja hi és al capdamunt del fitxer; si no, afegeix-l'hi.

- [ ] **Step 4: Executa'ls i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_config_and_storage.py -q`
Expected: FAIL amb `ImportError` / `AttributeError` sobre `build_tender_document_key`, `app.core.http` i els camps `DOCUMENT_*`.

- [ ] **Step 5: Crea `app/core/http.py`**

```python
"""Identitat HTTP de la plataforma davant de fonts externes.

Viu a `core` i no a `ingestion` perque hi ha dos consumidors: la sindicacio de
PLACSP i la descarrega de plecs. Les dues capes s'han de presentar igual davant
d'un servei public.
"""

from __future__ import annotations

from app.core.config import settings


def user_agent() -> str:
    """User-Agent identificable, amb contacte, tal com demana el bon us de dades obertes."""
    contact = settings.INGESTION_CONTACT_EMAIL or "sense-contacte"
    return f"LicitacionsBot/0.1 (+ingesta de dades obertes; contacte: {contact})"
```

- [ ] **Step 6: Afegeix la configuració**

A `apps/api/app/core/config.py`, just abans del bloc `# --- Regles de negoci ---`:

```python
    # --- Identitat davant de fonts externes ---
    # Adreca de contacte que s'anuncia al User-Agent. Buida = "sense-contacte".
    INGESTION_CONTACT_EMAIL: str = ""

    # --- Descarrega de plecs de licitacio ---
    # Aquests limits son l'unica autoritat: cap altre modul defineix numeros.
    DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS: float = 30.0
    # Sostre de tota l'operacio, reintents i redireccions incloses.
    DOCUMENT_DOWNLOAD_TOTAL_TIMEOUT_SECONDS: float = 120.0
    DOCUMENT_MAX_SIZE_MB: int = 60
    DOCUMENT_MAX_REDIRECTS: int = 5
    DOCUMENT_DOWNLOAD_MAX_ATTEMPTS: int = 3
    DOCUMENT_DOWNLOAD_BACKOFF_SECONDS: float = 2.0
    DOCUMENT_DOWNLOAD_ALLOWED_PORTS: str = "80,443"
    # NOMES per a tests: permet descarregar de 127.0.0.1 i xarxes privades.
    # A produccio es rebutja explicitament (vegeu el validator de sota).
    DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS: bool = False
    DOCUMENT_ALLOWED_MIME_TYPES: str = Field(
        default=(
            "application/pdf,"
            "application/xml,text/xml,"
            "application/zip,"
            "application/msword,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.ms-excel,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "text/plain"
        )
    )
    # De moment nomes se n'extreu text del PDF. La resta es guarda tal qual.
    DOCUMENT_TEXT_EXTRACTION_MIME_TYPES: str = "application/pdf"
    DOCUMENT_MAX_PAGES: int = 2000
    # Per sota d'aquesta mitjana de caracters per pagina, l'extraccio es "low_text".
    DOCUMENT_LOW_TEXT_CHARS_PER_PAGE: int = 100
    # Un processat en curs mes antic que aixo es considera mort i es pot reclamar.
    DOCUMENT_PROCESSING_STALE_SECONDS: int = 300
```

- [ ] **Step 7: Afegeix el validator i les propietats derivades**

Immediatament després del mètode `_secret_key_is_strong_enough`:

```python
    @model_validator(mode="after")
    def _private_downloads_are_never_allowed_in_production(self) -> "Settings":
        """Un downloader que accepta IP privades a produccio es una SSRF oberta.

        Es llanca en comptes de corregir el valor en silenci: una configuracio
        de seguretat mal posada ha de fer soroll, no desaparegue.
        """
        if self.is_production and self.DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS:
            raise ValueError(
                "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS nomes es pot activar fora de produccio."
            )
        return self
```

I amb la resta de propietats, després de `max_upload_size_bytes`:

```python
    @property
    def document_max_size_bytes(self) -> int:
        return self.DOCUMENT_MAX_SIZE_MB * 1024 * 1024

    @property
    def document_allowed_mime_types(self) -> set[str]:
        return {m.strip() for m in self.DOCUMENT_ALLOWED_MIME_TYPES.split(",") if m.strip()}

    @property
    def document_text_extraction_mime_types(self) -> set[str]:
        return {
            m.strip() for m in self.DOCUMENT_TEXT_EXTRACTION_MIME_TYPES.split(",") if m.strip()
        }

    @property
    def document_allowed_ports(self) -> set[int]:
        return {
            int(p.strip()) for p in self.DOCUMENT_DOWNLOAD_ALLOWED_PORTS.split(",") if p.strip()
        }
```

- [ ] **Step 8: Afegeix els dos errors de domini**

A `apps/api/app/core/errors.py`, després de `DuplicateDocumentError`:

```python
class ProcessingInProgressError(ConflictError):
    """Ja hi ha una descarrega en curs per a aquest document."""

    code = "document_processing_in_progress"


class NoContentToExtractError(ValidationError):
    """S'ha demanat re-extreure text d'un document que mai s'ha descarregat be."""

    code = "no_content_to_extract"
```

`NoContentToExtractError` ha d'anar **després** de `ValidationError` al fitxer; si `ValidationError` està definit més avall, mou el bloc perquè quedi després.

- [ ] **Step 9: Afegeix els quatre enums**

Al final de `apps/api/app/models/enums.py`:

```python
class DownloadStatus(StrEnum):
    """Estat de la descarrega d'una versio de document."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    """Estat d'una passada de parser sobre un blob."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    # El format no admet extraccio de text (XML, ZIP...). No es un error.
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    FAILED = "failed"


class ExtractionQuality(StrEnum):
    """Mesura determinista, no un score de confianca."""

    GOOD = "good"
    LOW_TEXT = "low_text"
    EMPTY = "empty"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class PageExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    EMPTY = "empty"
    FAILED = "failed"
```

- [ ] **Step 10: Afegeix el constructor de claus d'storage**

A `apps/api/app/services/storage.py`, just després de `build_storage_key`:

```python
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def build_tender_document_key(sha256: str, extension: str) -> str:
    """Clau adrecada pel contingut per als plecs publics.

    S'esclafa en dos nivells de dos caracters perque un prefix unic amb desenes
    de milers d'objectes es incomode de llistar i de migrar.

    A diferencia de `build_storage_key`, aqui NO hi ha `company_id`: els plecs
    son globals i els mateixos bytes es comparteixen entre tenants.
    """
    if not _SHA256_HEX.match(sha256):
        raise StorageError("SHA-256 no valid per a la clau d'emmagatzematge.")
    ext = re.sub(r"[^a-z0-9]", "", extension.lower().lstrip("."))[:10]
    suffix = f".{ext}" if ext else ""
    return f"tenders/documents/{sha256[0:2]}/{sha256[2:4]}/{sha256}{suffix}"
```

- [ ] **Step 11: Fes que la ingesta comparteixi el User-Agent**

A `apps/api/app/ingestion/placsp/client.py`, elimina la funció `user_agent` local i substitueix-la per una importació. Canvia:

```python
def user_agent() -> str:
    contact = getattr(settings, "INGESTION_CONTACT_EMAIL", "") or "sense-contacte"
    return f"LicitacionsBot/0.1 (+ingesta de dades obertes; contacte: {contact})"
```

per:

```python
# Es reexporta perque la ingesta i la descarrega de plecs s'han de presentar
# igual davant de PLACSP.
from app.core.http import user_agent  # noqa: F401  (reexportacio intencionada)
```

Col·loca aquest `import` amb la resta d'imports, al capdamunt, i deixa el comentari just a sobre. Si `settings` deixa de fer-se servir en aquest fitxer, treu-ne la importació.

- [ ] **Step 12: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_config_and_storage.py -q`
Expected: PASS.

- [ ] **Step 13: Executa la suite sencera i el linter**

Run: `cd apps/api && python -m pytest -q && python -m ruff check app tests`
Expected: PASS, 267 tests o més, cap error de ruff.

- [ ] **Step 14: Commit**

```bash
git add apps/api/pyproject.toml apps/api/app/core/http.py apps/api/app/core/config.py \
        apps/api/app/core/errors.py apps/api/app/models/enums.py \
        apps/api/app/services/storage.py apps/api/app/ingestion/placsp/client.py \
        apps/api/tests/test_config_and_storage.py
git commit -m "feat(documents): configuracio, enums i clau d'storage per contingut

Fonaments de la capa documental: limits centralitzats amb prefix DOCUMENT_,
els quatre enums d'estat, la clau d'storage adrecada pel SHA-256 i el
User-Agent compartit amb la ingesta. Cap logica de negoci encara.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---

## Task 2: Guarda SSRF (`url_guard`)

Valida una URL abans de connectar-hi. És l'única cosa que impedeix que una URL guardada a la base de dades es converteixi en una primitiva SSRF. Es cridarà **abans de cada salt de redirecció**, no només al principi.

**Files:**
- Create: `apps/api/app/services/url_guard.py`
- Test: `apps/api/tests/test_url_guard.py`

**Interfaces:**
- Consumes: `settings.document_allowed_ports`, `settings.DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS` (Task 1)
- Produces:
  - `Resolver = Callable[[str, int], list[str]]`
  - `class BlockedURLError(Exception)` amb atribut `.code: str`
  - `@dataclass(frozen=True, slots=True) class ValidatedURL: url: str; host: str; port: int; addresses: tuple[str, ...]`
  - `async def validate_url(url: str, *, resolver: Resolver | None = None) -> ValidatedURL`
  - `def is_blocked_address(raw: str) -> bool`

- [ ] **Step 1: Escriu els tests que fallen**

Crea `apps/api/tests/test_url_guard.py`:

```python
"""El downloader no pot convertir-se en una SSRF primitive.

Cap d'aquests tests toca la xarxa: el resolutor de DNS s'injecta.
"""

from __future__ import annotations

import pytest

from app.services.url_guard import BlockedURLError, is_blocked_address, validate_url

PUBLIC_IP = "93.184.216.34"


def resolver_returning(*addresses: str):
    """Resolutor fals: ignora el host i retorna sempre aquestes adreces."""

    def _resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return _resolve


async def test_accepts_a_public_https_url():
    result = await validate_url(
        "https://contrataciondelestado.es/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
    )

    assert result.host == "contrataciondelestado.es"
    assert result.port == 443
    assert result.addresses == (PUBLIC_IP,)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/plec.pdf",
        "gopher://example.com/",
        "data:application/pdf;base64,AAAA",
    ],
)
async def test_rejects_non_http_schemes(url: str):
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(url, resolver=resolver_returning(PUBLIC_IP))
    assert caught.value.code == "invalid_scheme"


async def test_rejects_embedded_credentials():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "https://user:pass@example.com/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
        )
    assert caught.value.code == "blocked_url"


async def test_rejects_unexpected_ports():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "http://example.com:8080/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
        )
    assert caught.value.code == "blocked_port"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",          # loopback
        "10.0.0.5",           # privada
        "172.16.3.9",         # privada
        "192.168.1.10",       # privada
        "169.254.169.254",    # metadata de cloud
        "100.64.0.1",         # CGNAT
        "0.0.0.0",            # unspecified
        "224.0.0.1",          # multicast
        "::1",                # loopback IPv6
        "fd00::1",            # ULA IPv6
        "fe80::1",            # link-local IPv6
        "::ffff:127.0.0.1",   # IPv4 mapejada dins d'IPv6
    ],
)
async def test_rejects_internal_addresses(address: str):
    assert is_blocked_address(address) is True

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https://intern.example.com/x.pdf", resolver=resolver_returning(address))
    assert caught.value.code == "blocked_url"


async def test_rejects_when_any_resolved_address_is_internal():
    """Un host que resol a una IP publica i una de privada NO es acceptable."""
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "https://mixt.example.com/x.pdf", resolver=resolver_returning(PUBLIC_IP, "127.0.0.1")
        )
    assert caught.value.code == "blocked_url"


async def test_rejects_ip_literals_that_are_internal_without_resolving():
    def explode(host: str, port: int) -> list[str]:
        raise AssertionError("una IP literal no s'ha de resoldre per DNS")

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("http://127.0.0.1/plec.pdf", resolver=explode)
    assert caught.value.code == "blocked_url"


async def test_reports_dns_failures_distinctly():
    def fail(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https://no-existeix.example/x.pdf", resolver=fail)
    assert caught.value.code == "dns_error"


async def test_rejects_urls_without_a_host():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https:///plec.pdf", resolver=resolver_returning(PUBLIC_IP))
    assert caught.value.code == "blocked_url"


async def test_private_addresses_are_allowed_when_the_test_flag_is_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS", True)

    result = await validate_url("http://127.0.0.1/plec.pdf", resolver=resolver_returning("127.0.0.1"))
    assert result.host == "127.0.0.1"
```

- [ ] **Step 2: Executa'ls i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_url_guard.py -q`
Expected: FAIL amb `ModuleNotFoundError: No module named 'app.services.url_guard'`.

- [ ] **Step 3: Implementa `url_guard.py`**

Crea `apps/api/app/services/url_guard.py`:

```python
"""Validacio d'URL externes abans de qualsevol connexio.

`TenderDocument.url` ve d'una font externa i viu a la base de dades. Res
d'aixo la fa segura: sense aquesta guarda, el backend seria un proxy que
qualsevol contingut ingerit pot dirigir cap a la xarxa interna, cap al
localhost o cap a l'endpoint de metadades del proveidor de cloud.

La validacio s'aplica ABANS DE CADA SALT, no nomes a la URL inicial: una
redireccio 302 cap a 169.254.169.254 es exactament l'atac que aixo evita.

Limitacio coneguda: entre la resolucio que fem aqui i la connexio real d'httpx
hi ha una finestra de DNS rebinding. Tancar-la del tot vol dir connectar-se a
la IP validada i sobreescriure la capcalera Host. Queda documentat com a deute
a `docs/documents.md`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import anyio

from app.core.config import settings

#: Resol un host a una llista d'adreces IP en text. Injectable per als tests.
Resolver = Callable[[str, int], list[str]]

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}

# `is_private` de la stdlib ja cobreix loopback, link-local i ULA, pero es
# preferible ser explicit amb les xarxes que a la practica son l'objectiu:
# les metadades del cloud i el rang CGNAT dels operadors.
CLOUD_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
EXTRA_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


class BlockedURLError(Exception):
    """La URL no es apta per descarregar-la."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def is_blocked_address(raw: str) -> bool:
    """Diu si una adreca IP cau en un rang que no volem que el downloader toqui."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        # Si no la sabem interpretar, no la deixem passar.
        return True

    # Una IPv4 disfressada d'IPv6 s'ha de jutjar com el que es.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    if address in CLOUD_METADATA_ADDRESSES:
        return True
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in EXTRA_BLOCKED_NETWORKS)
    return False


def _literal_address(host: str) -> str | None:
    """Retorna el host si ja es una IP literal (amb o sense claudators)."""
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


async def validate_url(url: str, *, resolver: Resolver | None = None) -> ValidatedURL:
    """Comprova esquema, credencials, port i totes les IP a les quals resol el host."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"Esquema no permes: {scheme or 'cap'}.", "invalid_scheme")
    if parts.username or parts.password:
        raise BlockedURLError("Les URL amb credencials no s'accepten.", "blocked_url")

    host = parts.hostname
    if not host:
        raise BlockedURLError("La URL no te host.", "blocked_url")

    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise BlockedURLError("Port no valid.", "blocked_port") from None
    if port not in settings.document_allowed_ports:
        raise BlockedURLError(f"Port no permes: {port}.", "blocked_port")

    allow_private = settings.DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS

    literal = _literal_address(host)
    if literal is not None:
        # Una IP literal no s'ha de resoldre: ja sabem cap a on va.
        if not allow_private and is_blocked_address(literal):
            raise BlockedURLError(f"Adreca no permesa: {literal}.", "blocked_url")
        return ValidatedURL(url=url, host=host, port=port, addresses=(literal,))

    resolve = resolver or default_resolver
    try:
        addresses = await anyio.to_thread.run_sync(resolve, host, port)
    except Exception as exc:  # noqa: BLE001 - qualsevol fallada de DNS es el mateix cas
        raise BlockedURLError(f"No s'ha pogut resoldre {host}.", "dns_error") from exc

    if not addresses:
        raise BlockedURLError(f"{host} no resol a cap adreca.", "dns_error")

    if not allow_private:
        for address in addresses:
            if is_blocked_address(address):
                raise BlockedURLError(
                    f"{host} resol a una adreca no permesa.", "blocked_url"
                )

    return ValidatedURL(url=url, host=host, port=port, addresses=tuple(addresses))
```

- [ ] **Step 4: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_url_guard.py -q`
Expected: PASS, 20+ tests.

- [ ] **Step 5: Linter**

Run: `cd apps/api && python -m ruff check app/services/url_guard.py tests/test_url_guard.py`
Expected: cap error.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/url_guard.py apps/api/tests/test_url_guard.py
git commit -m "feat(documents): guarda SSRF per a URL externes

Valida esquema, credencials, port i TOTES les IP a les quals resol el host,
incloent-hi metadades de cloud, CGNAT i IPv4 mapejades dins d'IPv6. El
resolutor s'injecta, aixi que cap test toca la xarxa.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 3: Descarregador streaming amb validació de contingut

Obté bytes i prou. No toca la base de dades ni l'storage. Fa streaming amb tall dur per mida, calcula el SHA-256 mentre baixa, segueix redireccions **manualment** revalidant-les, i decideix el tipus real pels magic bytes.

**Files:**
- Create: `apps/api/app/services/document_download.py`
- Test: `apps/api/tests/test_document_download.py`

**Interfaces:**
- Consumes: `validate_url`, `BlockedURLError`, `Resolver` (Task 2); `settings.DOCUMENT_*` i `app.core.http.user_agent` (Task 1); `app.services.files.MIME_EXTENSIONS`, `app.services.files.sanitize_filename` (ja existents)
- Produces:
  - `class DownloadError(Exception)` amb `.code: str` i `.http_status: int | None`
  - `@dataclass(frozen=True, slots=True) class DownloadResult` amb: `content: bytes`, `sha256: str`, `file_size: int`, `mime_type: str`, `declared_mime_type: str | None`, `detected_extension: str`, `original_filename: str`, `detected_filename: str | None`, `final_url: str`, `http_status: int`, `redirect_count: int`, `download_ms: int`
  - `async def download_document(url: str, *, client: httpx.AsyncClient | None = None, resolver: Resolver | None = None) -> DownloadResult`
  - `def sniff_mime_type(head: bytes) -> str | None`
  - `def resolve_mime_type(sniffed: str | None, declared: str | None, url: str) -> str`

- [ ] **Step 1: Escriu els tests que fallen**

Crea `apps/api/tests/test_document_download.py`:

```python
"""Descarrega de documents: nomes bytes, mai base de dades.

Tota la suite corre sense Internet. Els bytes venen d'un `MockTransport` i el
DNS d'un resolutor injectat. L'unic test que aixeca un socket de veritat es el
del tall per mida, perque un mock no demostra que el streaming s'aturi.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.core.config import settings
from app.services.document_download import (
    DownloadError,
    download_document,
    resolve_mime_type,
    sniff_mime_type,
)

PUBLIC_IP = "93.184.216.34"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Els reintents reals esperen segons. Als tests no cal esperar res."""
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_BACKOFF_SECONDS", 0.0)


def resolver_returning(*addresses: str):
    def _resolve(host: str, port: int) -> list[str]:
        return list(addresses or (PUBLIC_IP,))

    return _resolve


def client_for(handler) -> httpx.AsyncClient:
    """Client amb transport fals. `follow_redirects` s'ha de quedar desactivat."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_downloads_a_valid_pdf_and_hashes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
        )

    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.file_size == len(PDF_BYTES)
    assert result.mime_type == "application/pdf"
    assert result.detected_extension == "pdf"
    assert result.original_filename == "pcap.pdf"
    assert result.http_status == 200
    assert result.redirect_count == 0


async def test_reports_404_without_retrying():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/no.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "not_found"
    assert caught.value.http_status == 404
    assert len(calls) == 1, "un 404 no s'ha de reintentar"


async def test_retries_a_500_and_then_succeeds():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
        )

    assert len(calls) == 3
    assert result.http_status == 200


async def test_gives_up_after_the_configured_attempts():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert len(calls) == settings.DOCUMENT_DOWNLOAD_MAX_ATTEMPTS
    assert caught.value.code == "http_error"


async def test_reports_timeouts():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("massa lent", request=request)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "timeout"


async def test_refuses_a_declared_content_length_over_the_limit():
    too_big = settings.document_max_size_bytes + 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PDF_BYTES,
            headers={"content-type": "application/pdf", "content-length": str(too_big)},
        )

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/gros.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_large"


async def test_refuses_a_body_over_the_limit_without_content_length(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_MAX_SIZE_MB", 1)
    payload = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/gros.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_large"


async def test_rejects_html_pretending_to_be_a_pdf():
    html = b"<!DOCTYPE html><html><body>Sessio caducada</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


async def test_rejects_a_mime_type_outside_the_allow_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/imatge.png", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


async def test_follows_a_valid_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://example.com/final.pdf"})
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/redirect", client=client, resolver=resolver_returning()
        )

    assert result.redirect_count == 1
    assert result.final_url == "https://example.com/final.pdf"
    assert result.mime_type == "application/pdf"


async def test_blocks_a_redirect_towards_a_private_address():
    """L'atac clau: la URL inicial es publica i la redireccio apunta a metadades."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "blocked_url"


async def test_stops_after_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/una-altra"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/inici", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_many_redirects"


async def test_reads_the_filename_from_content_disposition():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PDF_BYTES,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="PCAP signat.pdf"',
            },
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/descarrega?id=42", client=client, resolver=resolver_returning()
        )

    assert result.detected_filename == "PCAP signat.pdf"


async def test_accepts_xml_because_placsp_publishes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'<?xml version="1.0"?><root/>', headers={"content-type": "text/xml"}
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/dades.xml", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == "application/xml"
    assert result.detected_extension == "xml"


async def test_keeps_the_declared_ooxml_type_over_the_generic_zip_sniff():
    declared = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"PK\x03\x04" + b"\x00" * 64, headers={"content-type": declared}
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/annex.docx", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == declared
    assert result.detected_extension == "docx"


async def test_rejects_an_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/buit.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


def test_sniffing_recognises_the_formats_that_matter():
    assert sniff_mime_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_mime_type(b"PK\x03\x04rest") == "application/zip"
    assert sniff_mime_type(b"  <!DOCTYPE html><html>") == "text/html"
    assert sniff_mime_type(b'<?xml version="1.0"?>') == "application/xml"
    assert sniff_mime_type(b"text pla sense magic") is None


def test_resolve_mime_type_refuses_a_pdf_url_serving_something_else():
    with pytest.raises(DownloadError) as caught:
        resolve_mime_type("text/html", "text/html", "https://example.com/pcap.pdf")
    assert caught.value.code == "invalid_content"


async def test_aborts_a_real_stream_that_exceeds_the_limit(monkeypatch):
    """Un mock no demostra que el streaming s'aturi de veritat; un socket si.

    El servidor intenta enviar 8 MB amb un limit d'1 MB. Si el tall funciona, el
    client tanca la connexio i el servidor rep un BrokenPipe.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - firma imposada per la stdlib
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            try:
                for _ in range(128):
                    self.wfile.write(b"x" * 65536)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # el client ha tallat: exactament el que volem provar

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    monkeypatch.setattr(settings, "DOCUMENT_MAX_SIZE_MB", 1)
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS", True)
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOWED_PORTS", f"80,443,{port}")

    try:
        with pytest.raises(DownloadError) as caught:
            await download_document(f"http://127.0.0.1:{port}/gros.pdf")
        assert caught.value.code == "too_large"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
```

- [ ] **Step 2: Executa'ls i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_document_download.py -q`
Expected: FAIL amb `ModuleNotFoundError: No module named 'app.services.document_download'`.

- [ ] **Step 3: Implementa `document_download.py`**

Crea `apps/api/app/services/document_download.py`:

```python
"""Descarrega de documents publics de licitacio.

Responsabilitat unica: **obtenir bytes validats**. No parseja, no toca la base
de dades i no puja res a l'storage.

Tres decisions que val la pena explicitar:

* **Streaming amb tall dur.** El SHA-256 es calcula chunk a chunk i la
  descarrega s'avorta en el moment que se supera el limit. Un plec inesperadament
  enorme no arriba mai a estar sencer a la RAM.
* **Redireccions manuals.** `follow_redirects` es queda desactivat perque cada
  salt s'ha de tornar a validar contra la guarda SSRF. Deixar que httpx les
  segueixi sol equivaldria a validar nomes la primera URL.
* **Els magic bytes manen.** L'ordre de confianca es magic bytes > Content-Type
  > extensio de la URL. Una URL que acaba en `.pdf` i retorna HTML es un error,
  no un PDF estrany.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx

from app.core.config import settings
from app.core.http import user_agent
from app.services.files import MIME_EXTENSIONS, sanitize_filename
from app.services.url_guard import BlockedURLError, Resolver, validate_url

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
CHUNK_SIZE = 64 * 1024

# Els tipus OOXML son fitxers ZIP: el sniffing no els sap distingir. Quan el
# servidor en declara un i el contingut es un ZIP, la declaracio no contradiu el
# sniffing, l'afina.
ZIP_REFINEMENTS = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

EXTRA_EXTENSIONS = {
    "application/xml": "xml",
    "text/xml": "xml",
    "text/html": "html",
}

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


class DownloadError(Exception):
    """La descarrega no ha donat un document utilitzable."""

    def __init__(self, message: str, code: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class DownloadResult:
    content: bytes
    sha256: str
    file_size: int
    #: Tipus real, decidit pels magic bytes.
    mime_type: str
    #: El que deia la capcalera, nomes com a metadada de diagnostic.
    declared_mime_type: str | None
    detected_extension: str
    original_filename: str
    detected_filename: str | None
    final_url: str
    http_status: int
    redirect_count: int
    download_ms: int


def sniff_mime_type(head: bytes) -> str | None:
    """Determina el tipus pels primers bytes. `None` si no hi ha cap firma coneguda."""
    # La norma PDF tolera fins a 1024 bytes de brossa abans de la capcalera.
    if b"%PDF-" in head[:1024]:
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    stripped = head.lstrip()[:512].lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "text/html"
    if stripped.startswith(b"<?xml"):
        return "application/xml"
    return None


def resolve_mime_type(sniffed: str | None, declared: str | None, url: str) -> str:
    """Decideix el tipus real. Llanca `DownloadError` si el contingut menteix."""
    path = urlsplit(url).path.lower()
    announces_pdf = declared == "application/pdf" or path.endswith(".pdf")
    if announces_pdf and sniffed != "application/pdf":
        raise DownloadError(
            "La URL anuncia un PDF pero el contingut descarregat no ho es.",
            "invalid_content",
        )
    if sniffed == "application/zip" and declared in ZIP_REFINEMENTS:
        return declared
    if sniffed:
        return sniffed
    if declared:
        return declared
    raise DownloadError("No s'ha pogut determinar el tipus del document.", "invalid_content")


def extension_for(mime_type: str) -> str:
    return MIME_EXTENSIONS.get(mime_type) or EXTRA_EXTENSIONS.get(mime_type, "bin")


def filename_from_url(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return sanitize_filename(name or "document")


def filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = _FILENAME_RE.search(header)
    return sanitize_filename(unquote(match.group(1))) if match else None


@dataclass(slots=True)
class _Attempt:
    status: int
    redirect_to: str | None
    body: bytes | None
    sha256: str | None
    headers: httpx.Headers


async def _attempt(client: httpx.AsyncClient, url: str, max_bytes: int) -> _Attempt:
    async with client.stream("GET", url) as response:
        if response.status_code in REDIRECT_STATUS:
            location = response.headers.get("location")
            if not location:
                raise DownloadError(
                    "Redireccio sense capcalera Location.",
                    "http_error",
                    http_status=response.status_code,
                )
            return _Attempt(
                response.status_code, str(response.url.join(location)), None, None, response.headers
            )

        if response.status_code != 200:
            return _Attempt(response.status_code, None, None, None, response.headers)

        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
            raise DownloadError(
                f"El document supera el limit de {settings.DOCUMENT_MAX_SIZE_MB} MB.", "too_large"
            )

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                # Sortir del context manager tanca la connexio: la descarrega
                # s'atura de veritat, no es llegeix la resta del cos.
                raise DownloadError(
                    f"El document supera el limit de {settings.DOCUMENT_MAX_SIZE_MB} MB.",
                    "too_large",
                )
            digest.update(chunk)
            chunks.append(chunk)

        return _Attempt(200, None, b"".join(chunks), digest.hexdigest(), response.headers)


async def _attempt_with_retries(
    client: httpx.AsyncClient, url: str, max_bytes: int, deadline: float
) -> _Attempt:
    attempts = settings.DOCUMENT_DOWNLOAD_MAX_ATTEMPTS
    last_code = "network_error"
    last_message = "No s'ha pogut connectar amb l'origen."

    for attempt in range(1, attempts + 1):
        try:
            result = await _attempt(client, url, max_bytes)
        except httpx.TimeoutException as exc:
            last_code, last_message = "timeout", f"Temps d'espera esgotat: {exc}"
        except httpx.HTTPError as exc:
            last_code, last_message = "network_error", f"{type(exc).__name__}: {exc}"
        else:
            if result.status in RETRYABLE_STATUS and attempt < attempts:
                last_code, last_message = "http_error", f"HTTP {result.status}"
            else:
                return result

        if attempt >= attempts or time.monotonic() >= deadline:
            break
        await asyncio.sleep(settings.DOCUMENT_DOWNLOAD_BACKOFF_SECONDS * attempt)

    raise DownloadError(last_message, last_code)


async def download_document(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> DownloadResult:
    """Descarrega i valida un document. Llanca `DownloadError` amb un codi estable."""
    started = time.monotonic()
    deadline = started + settings.DOCUMENT_DOWNLOAD_TOTAL_TIMEOUT_SECONDS
    max_bytes = settings.document_max_size_bytes

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": user_agent(), "Accept": "*/*"},
        )

    try:
        current = url
        redirects = 0

        while True:
            if time.monotonic() >= deadline:
                raise DownloadError("Temps total de descarrega esgotat.", "timeout")

            try:
                await validate_url(current, resolver=resolver)
            except BlockedURLError as exc:
                raise DownloadError(str(exc), exc.code) from exc

            attempt = await _attempt_with_retries(client, current, max_bytes, deadline)
            if attempt.redirect_to is None:
                break

            redirects += 1
            if redirects > settings.DOCUMENT_MAX_REDIRECTS:
                raise DownloadError(
                    f"Mes de {settings.DOCUMENT_MAX_REDIRECTS} redireccions.",
                    "too_many_redirects",
                )
            current = attempt.redirect_to

        if attempt.status == 404:
            raise DownloadError("El document no existeix a l'origen.", "not_found", http_status=404)
        if attempt.status != 200:
            raise DownloadError(
                f"L'origen ha respost HTTP {attempt.status}.",
                "http_error",
                http_status=attempt.status,
            )

        body = attempt.body or b""
        if not body:
            raise DownloadError("El document descarregat es buit.", "invalid_content")

        declared = (attempt.headers.get("content-type") or "").split(";")[0].strip().lower() or None
        mime_type = resolve_mime_type(sniff_mime_type(body[:2048]), declared, current)
        if mime_type not in settings.document_allowed_mime_types:
            raise DownloadError(f"Tipus de document no admes: {mime_type}.", "invalid_content")

        download_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "document.download.ok",
            extra={
                # Nomes el host: la query string d'un plec pot dur identificadors.
                "url_host": urlsplit(current).hostname,
                "http_status": attempt.status,
                "bytes": len(body),
                "sha256": attempt.sha256,
                "redirect_count": redirects,
                "download_ms": download_ms,
            },
        )

        return DownloadResult(
            content=body,
            sha256=attempt.sha256 or hashlib.sha256(body).hexdigest(),
            file_size=len(body),
            mime_type=mime_type,
            declared_mime_type=declared,
            detected_extension=extension_for(mime_type),
            original_filename=filename_from_url(url),
            detected_filename=filename_from_disposition(attempt.headers.get("content-disposition")),
            final_url=current,
            http_status=attempt.status,
            redirect_count=redirects,
            download_ms=download_ms,
        )
    finally:
        if owns_client:
            await client.aclose()
```

- [ ] **Step 4: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_document_download.py -q`
Expected: PASS, 19 tests, en menys de 15 segons.

- [ ] **Step 5: Linter i suite completa**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/document_download.py apps/api/tests/test_document_download.py
git commit -m "feat(documents): descarregador streaming amb validacio de contingut

SHA-256 calculat mentre baixa, tall dur per mida verificat contra un socket
real, redireccions seguides a ma i revalidades contra la guarda SSRF, i tipus
decidit pels magic bytes: una URL .pdf que retorna HTML es invalid_content.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 4: Generador de PDF de prova i extractor de text

Dues peces que van juntes perquè l'una prova l'altra. El generador escriu sintaxi PDF crua — cap dependència nova — i l'extractor converteix bytes en pàgines normalitzades amb una qualitat determinista.

**Files:**
- Create: `apps/api/tests/fixtures/pdf_builder.py`
- Create: `apps/api/app/services/pdf_text.py`
- Test: `apps/api/tests/test_pdf_text.py`

**Interfaces:**
- Consumes: `settings.DOCUMENT_MAX_PAGES`, `settings.DOCUMENT_LOW_TEXT_CHARS_PER_PAGE` (Task 1); `ExtractionStatus`, `ExtractionQuality`, `PageExtractionStatus` (Task 1)
- Produces:
  - `tests.fixtures.pdf_builder.build_pdf(pages: Sequence[str]) -> bytes`
  - `tests.fixtures.pdf_builder.build_corrupt_pdf() -> bytes`
  - `app.services.pdf_text.PARSER_VERSION: str` (`"pypdf-v1"`)
  - `@dataclass(frozen=True, slots=True) class ExtractedPage`: `page_number: int` (1-based), `text: str`, `char_count: int`, `text_hash: str`, `status: PageExtractionStatus`
  - `@dataclass(frozen=True, slots=True) class ExtractionResult`: `parser_version: str`, `status: ExtractionStatus`, `quality: ExtractionQuality`, `pages: tuple[ExtractedPage, ...]`, `page_count: int`, `extracted_page_count: int`, `empty_page_count: int`, `text_char_count: int`, `ocr_candidate: bool`, `parse_ms: int`, `error_code: str | None`, `error_message: str | None`
  - `def normalize_page_text(raw: str) -> str`
  - `def extract_pdf_text(data: bytes) -> ExtractionResult` (síncrona, CPU-bound)
  - `async def extract_pdf_text_async(data: bytes) -> ExtractionResult`
  - `def unsupported_result(mime_type: str) -> ExtractionResult`

- [ ] **Step 1: Escriu el generador de PDF**

Crea `apps/api/tests/fixtures/pdf_builder.py`:

```python
"""Generador de PDF minims per als tests.

Escriu sintaxi PDF crua a ma. Es fa aixi expressament per NO afegir cap
dependencia (reportlab, fpdf) nomes per generar fixtures: la unica biblioteca
de PDF del projecte ha de ser el parser.

Els PDF que en surten son valids: capcalera, objectes numerats, taula xref amb
els desplacaments reals, trailer i %%EOF. `pypdf` els obre sense queixar-se.
"""

from __future__ import annotations

from collections.abc import Sequence


def _escape(text: str) -> bytes:
    """Codifica a WinAnsi (cp1252) i escapa els caracters especials del PDF.

    cp1252 cobreix accents catalans i castellans: a e i o u amb accent, dieresi,
    c trencada i enya.
    """
    encoded = text.encode("cp1252", errors="replace")
    out = bytearray()
    for byte in encoded:
        char = bytes([byte])
        if char in (b"(", b")", b"\\"):
            out += b"\\"
        out += char
    return bytes(out)


def _content_stream(text: str) -> bytes:
    body = bytearray(b"BT\n/F1 12 Tf\n14 TL\n72 720 Td\n")
    for index, line in enumerate(text.split("\n")):
        if index:
            body += b"T*\n"
        body += b"(" + _escape(line) + b") Tj\n"
    body += b"ET\n"
    return bytes(body)


def build_pdf(pages: Sequence[str]) -> bytes:
    """Genera un PDF amb una pagina per element.

    Una cadena buida genera una pagina SENSE stream de continguts: es la manera
    honesta de simular una pagina escanejada, de la qual no es pot extreure text.
    """
    if not pages:
        raise ValueError("Un PDF necessita com a minim una pagina.")

    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    next_id = 4  # 1=Catalog, 2=Pages, 3=Font

    for text in pages:
        page_id = next_id
        next_id += 1
        contents = b""
        if text:
            stream_id = next_id
            next_id += 1
            data = _content_stream(text)
            objects[stream_id] = (
                b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"endstream"
            )
            contents = b" /Contents " + str(stream_id).encode() + b" 0 R"
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Resources << /Font << /F1 3 0 R >> >>" + contents + b" >>"
        )
        page_ids.append(page_id)

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    )
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += str(number).encode() + b" 0 obj\n" + objects[number] + b"\nendobj\n"

    xref_offset = len(out)
    size = max(objects) + 1
    out += b"xref\n0 " + str(size).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(size).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def build_corrupt_pdf() -> bytes:
    """Capcalera valida i cos truncat: passa el sniffing pero no el parser."""
    return build_pdf(["Contingut que no arriba a acabar"])[:150]
```

- [ ] **Step 2: Escriu els tests que fallen**

Crea `apps/api/tests/test_pdf_text.py`:

```python
"""Extraccio de text: pagines, no un string gegant.

La cita futura "PCAP, pagina 27" nomes val si la pagina 27 desada es de veritat
la 27 del document. Aquests tests ho fixen.
"""

from __future__ import annotations

import hashlib

import pytest

from app.core.config import settings
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services.pdf_text import (
    PARSER_VERSION,
    extract_pdf_text,
    normalize_page_text,
    unsupported_result,
)
from tests.fixtures.pdf_builder import build_corrupt_pdf, build_pdf

LOREM = (
    "Plec de clausules administratives particulars per a la contractacio del "
    "servei de manteniment integral dels equipaments municipals. "
) * 3


def test_extracts_text_from_a_single_page_pdf():
    result = extract_pdf_text(build_pdf([LOREM]))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == PARSER_VERSION
    assert result.page_count == 1
    assert len(result.pages) == 1
    assert "clausules administratives" in result.pages[0].text


def test_page_numbers_are_one_based_and_in_order():
    result = extract_pdf_text(build_pdf(["Primera " + LOREM, "Segona " + LOREM, "Tercera " + LOREM]))

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[0].text.startswith("Primera")
    assert result.pages[2].text.startswith("Tercera")


def test_keeps_catalan_and_spanish_accents():
    result = extract_pdf_text(build_pdf(["Adjudicacio provisional: numero d'expedient, accio, "
                                         "informacio, Espanya, forca, alcada " + LOREM]))

    text = result.pages[0].text
    for word in ("Adjudicacio", "expedient", "Espanya"):
        assert word in text


def test_marks_a_page_without_a_content_stream_as_empty():
    result = extract_pdf_text(build_pdf([LOREM, "", LOREM]))

    assert result.pages[1].status == PageExtractionStatus.EMPTY
    assert result.pages[1].text == ""
    assert result.pages[1].char_count == 0
    assert result.empty_page_count == 1
    assert result.extracted_page_count == 2


def test_hashes_the_normalised_text_of_each_page():
    result = extract_pdf_text(build_pdf([LOREM]))
    page = result.pages[0]

    assert page.text_hash == hashlib.sha256(page.text.encode("utf-8")).hexdigest()
    assert page.char_count == len(page.text)


def test_quality_is_good_for_a_normal_document():
    result = extract_pdf_text(build_pdf([LOREM, LOREM, LOREM]))

    assert result.quality == ExtractionQuality.GOOD
    assert result.ocr_candidate is False


def test_a_document_without_any_text_is_empty_and_an_ocr_candidate():
    """El cas del plec escanejat: 5 pagines, cap caracter."""
    result = extract_pdf_text(build_pdf(["", "", "", "", ""]))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.page_count == 5
    assert result.text_char_count == 0
    assert result.quality == ExtractionQuality.EMPTY
    assert result.ocr_candidate is True


def test_a_document_with_almost_no_text_is_low_text():
    result = extract_pdf_text(build_pdf(["Annex I", "Annex II", "Annex III"]))

    assert result.quality == ExtractionQuality.LOW_TEXT
    assert result.ocr_candidate is True


def test_more_than_half_the_pages_empty_is_low_text():
    result = extract_pdf_text(build_pdf([LOREM, "", "", ""]))

    assert result.quality == ExtractionQuality.LOW_TEXT
    assert result.ocr_candidate is True


def test_a_corrupt_pdf_fails_without_raising():
    result = extract_pdf_text(build_corrupt_pdf())

    assert result.status == ExtractionStatus.FAILED
    assert result.quality == ExtractionQuality.FAILED
    assert result.error_code == "corrupt_pdf"
    assert result.pages == ()


def test_bytes_that_are_not_a_pdf_at_all_fail():
    result = extract_pdf_text(b"aixo no es cap PDF")

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "corrupt_pdf"


def test_refuses_a_document_with_too_many_pages(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 2)

    result = extract_pdf_text(build_pdf([LOREM, LOREM, LOREM]))

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "too_many_pages"


def test_handles_a_large_document_within_the_limit():
    """40 pagines: prou per detectar una implementacio quadratica."""
    result = extract_pdf_text(build_pdf([LOREM] * 40))

    assert result.page_count == 40
    assert result.pages[-1].page_number == 40
    assert result.quality == ExtractionQuality.GOOD


def test_unsupported_result_describes_a_non_pdf():
    result = unsupported_result("application/zip")

    assert result.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert result.quality == ExtractionQuality.NOT_APPLICABLE
    assert result.error_code == "unsupported_for_text_extraction"
    assert result.ocr_candidate is False
    assert result.pages == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("linia   amb   espais", "linia   amb   espais"),  # no es col·lapsen espais interns
        ("final amb espais   \nsegona", "final amb espais\nsegona"),
        ("windows\r\nnewline", "windows\nnewline"),
        ("mac\rnewline", "mac\nnewline"),
        ("massa\n\n\n\n\nbuides", "massa\n\nbuides"),
        ("amb\x00nuls\x07dins", "ambnulsdins"),
        ("\n\n  vora  \n\n", "  vora"),
        ("tabulador\tconservat", "tabulador\tconservat"),
    ],
)
def test_normalisation_is_conservative(raw: str, expected: str):
    assert normalize_page_text(raw) == expected


def test_normalisation_composes_unicode():
    # "e" + accent combinant ha de quedar com un sol punt de codi.
    assert normalize_page_text("é") == "é"
```

- [ ] **Step 3: Executa'ls i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_pdf_text.py -q`
Expected: FAIL amb `ModuleNotFoundError: No module named 'app.services.pdf_text'`.

Comprova que existeix `apps/api/tests/fixtures/__init__.py`; si no, crea'l buit perquè `from tests.fixtures.pdf_builder import ...` funcioni.

- [ ] **Step 4: Implementa `pdf_text.py`**

Crea `apps/api/app/services/pdf_text.py`:

```python
"""Extraccio de text de PDF, pagina a pagina.

Biblioteca: **pypdf**, llicencia BSD-3-Clause. El criteri decisiu es la
llicencia: PyMuPDF es AGPL i, en un servei accessible per xarxa, obligaria a
publicar el codi del producte sencer. pypdf a mes es Python pur, aixi que no
afegeix cap dependencia de sistema ni al Dockerfile ni al desenvolupament a
Windows.

Es guarda `parser_version` a cada extraccio: si algun dia canviem de biblioteca
o d'estrategia, es podra tornar a extreure el text sobre els mateixos bytes
sense destruir el que ja teniem.

El resultat NO es un string gegant. La separacio per pagina es el requisit:
sense ella, una cita futura del tipus "solvencia economica, PCAP pagina 27" no
es pot verificar.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
import unicodedata
from dataclasses import dataclass

import anyio
from pypdf import PdfReader

from app.core.config import settings
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus

logger = logging.getLogger(__name__)

#: Identifica com s'ha extret el text. Canviar de parser o d'estrategia obliga
#: a pujar aquesta versio: es el que fa possible re-extreure sense re-descarregar.
PARSER_VERSION = "pypdf-v1"

# Es conserven el salt de linia i el tabulador; la resta de caracters de control
# no aporten res i embruten el text.
_CONTROL_CODES = frozenset(set(range(32)) - {9, 10} | {127})
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    #: 1-based, com la pagina que veu l'usuari. Mai 0-based.
    page_number: int
    text: str
    char_count: int
    text_hash: str
    status: PageExtractionStatus


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    parser_version: str
    status: ExtractionStatus
    quality: ExtractionQuality
    pages: tuple[ExtractedPage, ...]
    page_count: int
    extracted_page_count: int
    empty_page_count: int
    text_char_count: int
    ocr_candidate: bool
    parse_ms: int
    error_code: str | None
    error_message: str | None


def normalize_page_text(raw: str) -> str:
    """Neteja minima i conservadora.

    NO es col·lapsen els espais interns: en un plec, l'alineacio de columnes es
    informacio util per a la fase seguent. Aixo no resumeix ni reinterpreta res.
    """
    text = unicodedata.normalize("NFC", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if ord(char) not in _CONTROL_CODES)
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip("\n")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_quality(
    page_count: int, empty_page_count: int, text_char_count: int
) -> tuple[ExtractionQuality, bool]:
    """Qualitat determinista i el senyal `ocr_candidate`.

    `ocr_candidate` es un INDICI, no una prova: un plec pot ser legitimament
    curt. La decisio de fer OCR es posterior, mes cara i separada.
    """
    if page_count <= 0:
        return ExtractionQuality.EMPTY, False
    if text_char_count == 0:
        return ExtractionQuality.EMPTY, True
    mean_chars = text_char_count / page_count
    empty_ratio = empty_page_count / page_count
    if mean_chars < settings.DOCUMENT_LOW_TEXT_CHARS_PER_PAGE or empty_ratio > 0.5:
        return ExtractionQuality.LOW_TEXT, True
    return ExtractionQuality.GOOD, False


def _failed(code: str, message: str, started: float) -> ExtractionResult:
    return ExtractionResult(
        parser_version=PARSER_VERSION,
        status=ExtractionStatus.FAILED,
        quality=ExtractionQuality.FAILED,
        pages=(),
        page_count=0,
        extracted_page_count=0,
        empty_page_count=0,
        text_char_count=0,
        ocr_candidate=False,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=code,
        error_message=message[:500],
    )


def unsupported_result(mime_type: str) -> ExtractionResult:
    """Resultat per a un format del qual no n'extreiem text (XML, ZIP, DOCX...).

    No es un error: l'original s'ha guardat correctament, simplement no en
    llegim el text en aquesta iteracio.
    """
    return ExtractionResult(
        parser_version=PARSER_VERSION,
        status=ExtractionStatus.SKIPPED_UNSUPPORTED,
        quality=ExtractionQuality.NOT_APPLICABLE,
        pages=(),
        page_count=0,
        extracted_page_count=0,
        empty_page_count=0,
        text_char_count=0,
        ocr_candidate=False,
        parse_ms=0,
        error_code="unsupported_for_text_extraction",
        error_message=f"No s'extreu text de {mime_type} en aquesta versio.",
    )


def extract_pdf_text(data: bytes) -> ExtractionResult:
    """Extreu el text d'un PDF. Sincrona i CPU-bound: crida-la amb `extract_pdf_text_async`.

    Mai llanca: qualsevol fallada torna un `ExtractionResult` amb `error_code`.
    """
    started = time.monotonic()

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001 - qualsevol PDF trencat acaba aqui
        return _failed("corrupt_pdf", f"{type(exc).__name__}: {exc}", started)

    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            return _failed("encrypted_pdf", f"{type(exc).__name__}: {exc}", started)
        if not opened:
            return _failed("encrypted_pdf", "El PDF esta protegit amb contrasenya.", started)

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        return _failed("corrupt_pdf", f"{type(exc).__name__}: {exc}", started)

    if page_count == 0:
        return _failed("corrupt_pdf", "El PDF no declara cap pagina.", started)
    if page_count > settings.DOCUMENT_MAX_PAGES:
        return _failed(
            "too_many_pages",
            f"{page_count} pagines superen el limit de {settings.DOCUMENT_MAX_PAGES}.",
            started,
        )

    pages: list[ExtractedPage] = []
    empty = 0
    extracted = 0
    total_chars = 0

    for index in range(page_count):
        number = index + 1
        try:
            raw = reader.pages[index].extract_text() or ""
        except Exception:  # noqa: BLE001 - una pagina trencada no tomba el document
            logger.warning("document.extract.page_failed", extra={"page": number})
            pages.append(ExtractedPage(number, "", 0, _hash_text(""), PageExtractionStatus.FAILED))
            continue

        text = normalize_page_text(raw)
        if text:
            extracted += 1
            total_chars += len(text)
            status = PageExtractionStatus.EXTRACTED
        else:
            empty += 1
            status = PageExtractionStatus.EMPTY
        pages.append(ExtractedPage(number, text, len(text), _hash_text(text), status))

    quality, ocr_candidate = evaluate_quality(page_count, empty, total_chars)

    return ExtractionResult(
        parser_version=PARSER_VERSION,
        status=ExtractionStatus.EXTRACTED,
        quality=quality,
        pages=tuple(pages),
        page_count=page_count,
        extracted_page_count=extracted,
        empty_page_count=empty,
        text_char_count=total_chars,
        ocr_candidate=ocr_candidate,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=None,
        error_message=None,
    )


async def extract_pdf_text_async(data: bytes) -> ExtractionResult:
    """Executa el parser en un fil: es sincron i CPU-bound, no ha de bloquejar el loop."""
    return await anyio.to_thread.run_sync(extract_pdf_text, data)
```

- [ ] **Step 5: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_pdf_text.py -q`
Expected: PASS.

Si `test_a_document_with_almost_no_text_is_low_text` falla perquè la mitjana surt per sobre de 100 caràcters, escurça els textos del test; si falla al revés, allarga `LOREM`. No toquis el llindar de configuració per fer passar un test.

- [ ] **Step 6: Verifica que els PDF generats són vàlids de veritat**

Run:
```bash
cd apps/api && python -c "
from pypdf import PdfReader
import io
from tests.fixtures.pdf_builder import build_pdf
data = build_pdf(['Pagina una amb text', '', 'Pagina tres'])
r = PdfReader(io.BytesIO(data))
print('pagines:', len(r.pages))
print('p1:', repr(r.pages[0].extract_text()))
print('p2:', repr(r.pages[1].extract_text()))
"
```
Expected: `pagines: 3`, la pàgina 1 amb text i la 2 buida. Si `pypdf` avisa d'un xref invàlid, revisa els desplaçaments de `build_pdf`.

- [ ] **Step 7: Linter i suite completa**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/pdf_text.py apps/api/tests/test_pdf_text.py \
        apps/api/tests/fixtures/pdf_builder.py
git commit -m "feat(documents): extraccio de text per pagina amb pypdf

Pagines 1-based, normalitzacio conservadora que no col·lapsa espais interns,
i qualitat determinista (good/low_text/empty/failed) amb ocr_candidate com a
indici, no com a prova. Una pagina trencada no tomba el document.

Els fixtures son PDF minims escrits amb sintaxi crua: cap dependencia nova
nomes per generar fitxers de prova.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 5: Models i migració

Les quatre entitats globals i la migració `0005`. Només taules noves: res del que ja hi ha a producció (1.188 Tenders, SourceRecords, pre-fits, tenant Demo) es toca.

**Files:**
- Create: `apps/api/app/models/tender_documents.py`
- Create: `apps/api/alembic/versions/0005_tender_document_files.py`
- Modify: `apps/api/app/models/__init__.py`
- Test: `apps/api/tests/test_tender_document_models.py`

**Interfaces:**
- Consumes: `Base`, `UUIDPrimaryKeyMixin`, `TimestampMixin`, `enum_column` (`app.db.base`); els quatre enums (Task 1)
- Produces: `app.models.tender_documents.TenderDocumentContent`, `TenderDocumentExtraction`, `TenderDocumentPage`, `TenderDocumentFile`

- [ ] **Step 1: Escriu els tests que fallen**

Crea `apps/api/tests/test_tender_document_models.py`:

```python
"""Invariants d'esquema de la capa documental.

Els que importen: les pagines son 1-based, els plecs son globals (cap
company_id), i els indexs unics parcials fan de lock i de garantia de
deduplicacio.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)


def make_content(sha: str) -> TenderDocumentContent:
    return TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/{sha[0:2]}/{sha[2:4]}/{sha}.pdf",
        file_size=1024,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )


def make_extraction(content_id: uuid.UUID, version: str = "pypdf-v1") -> TenderDocumentExtraction:
    return TenderDocumentExtraction(
        content_id=content_id,
        parser_version=version,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        empty_page_count=0,
        text_char_count=100,
        ocr_candidate=False,
        is_current=True,
    )


def test_the_documental_tables_are_global():
    """Si 100 empreses miren la mateixa licitacio, el plec es processa una vegada."""
    for model in (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentPage,
        TenderDocumentFile,
    ):
        assert "company_id" not in model.__table__.columns, f"{model.__name__} no pot ser de tenant"


async def test_the_same_sha256_cannot_be_stored_twice(session):
    sha = "a" * 64
    session.add(make_content(sha))
    await session.commit()

    session.add(make_content(sha))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_content_has_at_most_one_extraction_per_parser_version(session):
    content = make_content("b" * 64)
    session.add(content)
    await session.commit()

    session.add(make_extraction(content.id))
    await session.commit()

    duplicate = make_extraction(content.id)
    duplicate.is_current = False
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_content_has_at_most_one_current_extraction(session):
    content = make_content("c" * 64)
    session.add(content)
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v1"))
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v2"))  # tambe is_current=True
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_page_numbers_start_at_one(session):
    content = make_content("d" * 64)
    session.add(content)
    await session.commit()
    extraction = make_extraction(content.id)
    session.add(extraction)
    await session.commit()

    session.add(
        TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=0,
            text="",
            char_count=0,
            text_hash="0" * 64,
            status=PageExtractionStatus.EMPTY,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_page_number_is_unique_within_an_extraction(session):
    content = make_content("e" * 64)
    session.add(content)
    await session.commit()
    extraction = make_extraction(content.id)
    session.add(extraction)
    await session.commit()

    for _ in range(2):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=1,
                text="text",
                char_count=4,
                text_hash="1" * 64,
                status=PageExtractionStatus.EXTRACTED,
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_only_one_download_can_be_in_flight_per_document(session, tender_document):
    """Aquest index parcial es el lock de concurrencia: sense Redis i sense cua."""
    for _ in range(2):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                source_url="https://example.com/pcap.pdf",
                version_number=1,
                is_current=False,
                download_status=DownloadStatus.DOWNLOADING,
                started_at=sa.func.now(),
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_finished_versions_can_coexist(session, tender_document):
    """El cas B del versionat: contingut canviat, dues versions conservades."""
    for version in (1, 2):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                source_url="https://example.com/pcap.pdf",
                version_number=version,
                is_current=version == 2,
                download_status=DownloadStatus.DOWNLOADED,
                started_at=sa.func.now(),
            )
        )
    await session.commit()

    total = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderDocumentFile)
    )
    assert total == 2
```

- [ ] **Step 2: Afegeix la fixture `tender_document` a `conftest.py`**

A `apps/api/tests/conftest.py`, després de les fixtures existents:

```python
@pytest.fixture
async def tender_document(session):
    """Un TenderDocument minim, amb la seva Source i el seu Tender.

    Els plecs son globals: aquesta fixture NO crea cap Company.
    """
    import sqlalchemy as sa  # noqa: F401 - ja importat a dalt, es deixa explicit

    from app.models.enums import TenderDocumentType
    from app.models.sources import Source
    from app.models.tenders import Tender, TenderDocument

    source = Source(code=f"test-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()

    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Servei de manteniment integral dels equipaments municipals",
    )
    session.add(tender)
    await session.flush()

    document = TenderDocument(
        tender_id=tender.id,
        name="PCAP.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/pcap.pdf",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document
```

Comprova la signatura real de `Source` a `app/models/sources.py` abans d'executar-ho i ajusta els camps obligatoris que hi falten. Si `Source` en demana més (per exemple `base_url`), afegeix-los amb valors de prova.

- [ ] **Step 3: Executa els tests i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_tender_document_models.py -q`
Expected: FAIL amb `ModuleNotFoundError: No module named 'app.models.tender_documents'`.

- [ ] **Step 4: Escriu els models**

Crea `apps/api/app/models/tender_documents.py`:

```python
"""Capa documental dels plecs: original immutable i text per pagina.

Quatre entitats i una cadena que no es pot trencar:

    Tender -> TenderDocument -> File(versio) -> Content(sha256) -> Extraction -> Page

Totes son PUBLIQUES i GLOBALS: cap porta `company_id`. Si cent empreses
analitzen la mateixa licitacio, el PDF es baixa i es parseja una sola vegada.
La relacio empresa-requisit sera una taula de tenant, pero d'una altra iteracio.

Per que quatre taules i no una:

* `Content` es el blob adrecat pel SHA-256. Es la unitat de deduplicacio: els
  mateixos bytes = un sol objecte a l'storage, apuntin-hi els documents que
  apuntin.
* `Extraction` es UNA passada de parser sobre aquell blob. Canviar de
  `parser_version` crea una extraccio nova i conserva l'anterior sencera: el
  text ja extret no s'ha de poder perdre per un canvi de biblioteca.
* `Page` penja de l'extraccio, no del contingut, per la mateixa rao.
* `File` es la versio: que s'ha baixat, d'on, quan i amb quin resultat HTTP.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)

# Els indexs unics parcials s'han de declarar per als dos dialectes: la suite
# corre sobre SQLite i produccio sobre PostgreSQL, i la sintaxi del predicat
# booleà no coincideix.
_IN_FLIGHT = "download_status IN ('pending', 'downloading')"


class TenderDocumentContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bytes descarregats, identificats pel seu SHA-256.

    No te cap estat d'extraccio: aquesta taula nomes sap fets immutables sobre
    una seqüencia de bytes.
    """

    __tablename__ = "tender_document_contents"
    __table_args__ = (
        sa.UniqueConstraint("sha256", name="uq_tender_document_contents_sha256"),
    )

    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Clau interna de l'storage. MAI s'exposa per l'API.
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    #: Tipus DETECTAT pels magic bytes, no el que declarava el servidor.
    mime_type: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    detected_extension: Mapped[str | None] = mapped_column(sa.String(10))
    is_text_extractable: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    extractions: Mapped[list[TenderDocumentExtraction]] = relationship(
        back_populates="content", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderDocumentContent {self.sha256[:12]}>"


class TenderDocumentExtraction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una passada de parser sobre un `Content`.

    `UNIQUE (content_id, parser_version)`: una versio de parser, una extraccio.
    Un re-extract amb la mateixa versio substitueix les pagines d'aquesta fila,
    perque el parser es determinista i repetir-lo nomes te sentit per recuperar
    un error. Un canvi de versio, en canvi, crea una fila nova.
    """

    __tablename__ = "tender_document_extractions"
    __table_args__ = (
        sa.UniqueConstraint(
            "content_id", "parser_version", name="uq_tender_document_extractions_content_parser"
        ),
        sa.Index("ix_tender_document_extractions_content_id", "content_id"),
        sa.Index(
            "uq_tender_document_extractions_current",
            "content_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_contents.id", ondelete="CASCADE"),
        nullable=False,
    )
    parser_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    status: Mapped[ExtractionStatus] = mapped_column(
        enum_column(ExtractionStatus, "extraction_status"),
        nullable=False,
        default=ExtractionStatus.PENDING,
    )
    quality: Mapped[ExtractionQuality] = mapped_column(
        enum_column(ExtractionQuality, "extraction_quality"),
        nullable=False,
        default=ExtractionQuality.NOT_APPLICABLE,
    )
    page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    extracted_page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    empty_page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    text_char_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Indici, no prova: poc text NO demostra que calgui OCR.
    ocr_candidate: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    parse_ms: Mapped[int | None] = mapped_column(sa.Integer)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    content: Mapped[TenderDocumentContent] = relationship(back_populates="extractions")
    pages: Mapped[list[TenderDocumentPage]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="TenderDocumentPage.page_number",
    )


class TenderDocumentPage(UUIDPrimaryKeyMixin, Base):
    """El text d'una pagina.

    `page_number` es **1-based**, igual a la base de dades i a la interficie.
    Aquesta decisio no es estetica: la fase seguent haura de poder afirmar
    "solvencia economica, PCAP pagina 27" i que sigui verificable. Amb una capa
    de traduccio entremig, un off-by-one seria invisible fins que algu obris el
    PDF i comptes.
    """

    __tablename__ = "tender_document_pages"
    __table_args__ = (
        sa.UniqueConstraint(
            "extraction_id", "page_number", name="uq_tender_document_pages_extraction_page"
        ),
        sa.CheckConstraint("page_number >= 1", name="page_number_is_one_based"),
        sa.Index("ix_tender_document_pages_extraction_id", "extraction_id"),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    char_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    text_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[PageExtractionStatus] = mapped_column(
        enum_column(PageExtractionStatus, "page_extraction_status"),
        nullable=False,
        default=PageExtractionStatus.EMPTY,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    extraction: Mapped[TenderDocumentExtraction] = relationship(back_populates="pages")


class TenderDocumentFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una versio fisica d'un `TenderDocument`: que s'ha baixat, d'on i quan.

    Una URL NO es un fitxer immutable per sempre. Un organ pot substituir un
    plec sense canviar l'enllac, i llavors cal conservar les dues versions.

    L'index unic parcial sobre els estats en curs es el lock de concurrencia:
    dos requests simultanis no poden reclamar el mateix document, i el segon
    rep un 409 en comptes de duplicar la feina.
    """

    __tablename__ = "tender_document_files"
    __table_args__ = (
        sa.UniqueConstraint(
            "tender_document_id", "version_number", name="uq_tender_document_files_document_version"
        ),
        sa.Index("ix_tender_document_files_tender_document_id", "tender_document_id"),
        sa.Index("ix_tender_document_files_content_id", "content_id"),
        sa.Index(
            "uq_tender_document_files_in_flight",
            "tender_document_id",
            unique=True,
            postgresql_where=sa.text(_IN_FLIGHT),
            sqlite_where=sa.text(_IN_FLIGHT),
        ),
    )

    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT i no CASCADE: un blob referenciat no s'ha de poder esborrar.
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_contents.id", ondelete="RESTRICT"),
    )
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("source_records.id", ondelete="SET NULL")
    )

    source_url: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    final_url: Mapped[str | None] = mapped_column(sa.String(1000))
    original_filename: Mapped[str | None] = mapped_column(sa.String(255))
    detected_filename: Mapped[str | None] = mapped_column(sa.String(255))
    declared_mime_type: Mapped[str | None] = mapped_column(sa.String(160))

    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    download_status: Mapped[DownloadStatus] = mapped_column(
        enum_column(DownloadStatus, "download_status"),
        nullable=False,
        default=DownloadStatus.PENDING,
    )
    http_status: Mapped[int | None] = mapped_column(sa.Integer)
    redirect_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    download_ms: Mapped[int | None] = mapped_column(sa.Integer)
    downloaded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Quan s'ha reclamat el processat. Permet detectar processos morts.
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    #: Ultima vegada que una re-descarrega ha confirmat el mateix hash.
    last_checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    content: Mapped[TenderDocumentContent | None] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderDocumentFile v{self.version_number} {self.download_status}>"
```

- [ ] **Step 5: Registra els models**

A `apps/api/app/models/__init__.py`, afegeix la importació després de la de `app.models.tenders`:

```python
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
```

I dins de `__all__`, en ordre alfabètic, just després de `"TenderDocument"`:

```python
    "TenderDocumentContent",
    "TenderDocumentExtraction",
    "TenderDocumentFile",
    "TenderDocumentPage",
```

- [ ] **Step 6: Executa els tests dels models**

Run: `cd apps/api && python -m pytest tests/test_tender_document_models.py -q`
Expected: PASS.

- [ ] **Step 7: Genera la migració**

Run:
```bash
cd apps/api && python -m alembic revision --autogenerate -m "tender document files"
```

Reanomena el fitxer generat a `alembic/versions/0005_tender_document_files.py` i edita'n la capçalera perquè quedi:

```python
"""Capa documental: original immutable i text per pagina.

Afegeix quatre taules GLOBALS (cap porta `company_id`):

* `tender_document_contents`: el blob, adrecat pel SHA-256. Unitat de
  deduplicacio: els mateixos bytes son un sol objecte a l'storage.
* `tender_document_extractions`: una passada de parser sobre un blob. Canviar
  `parser_version` crea una fila nova i conserva l'anterior.
* `tender_document_pages`: el text, 1-based, penjant de l'extraccio.
* `tender_document_files`: la versio, que lliga un `TenderDocument` amb un
  contingut concret.

NO altera cap taula existent. Els 1.188 Tenders, els SourceRecords, els
pre-fits i les dades del tenant Demo queden intactes.

Revision ID: 0005_tender_docs
Revises: 0004_prefit
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005_tender_docs'
down_revision: str | None = '0004_prefit'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
```

- [ ] **Step 8: Revisa la migració generada a mà**

Comprova, i corregeix si cal:

1. **Només `op.create_table` i `op.create_index`.** Si l'autogenerate ha emès cap `op.alter_column`, `op.drop_*` o `op.add_column` sobre una taula que ja existia, **esborra-ho**: significa que ha detectat deriva prèvia i no és feina d'aquesta migració.
2. Els dos índexs únics parcials porten el predicat. L'autogenerate sovint els emet sense. Han de ser exactament:

```python
    op.create_index(
        "uq_tender_document_extractions_current",
        "tender_document_extractions",
        ["content_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_index(
        "uq_tender_document_files_in_flight",
        "tender_document_files",
        ["tender_document_id"],
        unique=True,
        postgresql_where=sa.text("download_status IN ('pending', 'downloading')"),
        sqlite_where=sa.text("download_status IN ('pending', 'downloading')"),
    )
```

3. El `downgrade()` elimina les quatre taules en ordre invers de dependència:

```python
def downgrade() -> None:
    op.drop_table('tender_document_pages')
    op.drop_table('tender_document_files')
    op.drop_table('tender_document_extractions')
    op.drop_table('tender_document_contents')
```

- [ ] **Step 9: Verifica que la migració coincideix amb els models**

Run: `cd apps/api && python -m pytest tests/test_migrations.py -q`
Expected: PASS. Si falla, indica una columna que és al model i no a la migració (o al revés): corregeix la migració, mai el test.

- [ ] **Step 10: Prova upgrade i downgrade sobre PostgreSQL real**

Run:
```bash
docker compose up -d db minio
cd apps/api && python -m alembic upgrade head
python -m alembic downgrade 0004_prefit
python -m alembic upgrade head
```
Expected: les tres ordres acaben sense error. Després verifica que no s'ha perdut res:

```bash
cd apps/api && python -c "
import asyncio, sqlalchemy as sa
from app.db.session import engine
async def main():
    async with engine.connect() as c:
        for table in ('tenders', 'source_records', 'company_tender_matches', 'companies'):
            n = await c.scalar(sa.text(f'SELECT count(*) FROM {table}'))
            print(f'{table}: {n}')
asyncio.run(main())
"
```
Expected: `tenders: 1188` i la resta dels valors previs a la migració. Si algun número ha caigut, atura't: la migració està tocant el que no toca.

- [ ] **Step 11: Suite completa i linter**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add apps/api/app/models/tender_documents.py apps/api/app/models/__init__.py \
        apps/api/alembic/versions/0005_tender_document_files.py \
        apps/api/tests/test_tender_document_models.py apps/api/tests/conftest.py
git commit -m "feat(documents): models i migracio de la capa documental

Quatre taules globals: Content adrecat pel SHA-256, Extraction versionada per
parser_version, Page 1-based i File com a versio del TenderDocument. Dos
indexs unics parcials fan de lock de concurrencia i de garantia d'una sola
extraccio vigent per contingut.

La migracio nomes crea taules: cap dada existent es toca.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 6: Orquestració — dedup, versionat i concurrència

L'única peça que toca la base de dades i l'storage. Coordina descàrrega, deduplicació per hash, versionat, extracció i estats, amb transaccions curtes i cap HTTP a dins de cap d'elles.

**Files:**
- Create: `apps/api/app/services/tender_document_processing.py`
- Test: `apps/api/tests/test_tender_document_processing.py`

**Interfaces:**
- Consumes: `download_document`, `DownloadResult`, `DownloadError` (Task 3); `extract_pdf_text_async`, `unsupported_result`, `PARSER_VERSION`, `ExtractionResult` (Task 4); els quatre models (Task 5); `get_storage`, `build_tender_document_key` (Task 1); `ProcessingInProgressError`, `NoContentToExtractError` (Task 1)
- Produces:
  - `@dataclass(frozen=True, slots=True) class ProcessOutcome`: `file: TenderDocumentFile`, `reused: bool`
  - `async def process_tender_document(session, document, *, force_download=False, force_reextract=False, client=None, resolver=None) -> ProcessOutcome`
  - `async def current_file_for(session, document_id: uuid.UUID) -> TenderDocumentFile | None`
  - `async def current_extraction_for(session, content_id: uuid.UUID) -> TenderDocumentExtraction | None`

- [ ] **Step 1: Escriu els tests que fallen**

Crea `apps/api/tests/test_tender_document_processing.py`:

```python
"""Deduplicacio, versionat i concurrencia del processat de plecs.

Aquests tests fixen les regles que costen mes de recuperar si es trenquen: que
cap original es perdi, que els mateixos bytes no es parsegin dues vegades, i
que dos clics simultanis no facin dues descarregues.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
import sqlalchemy as sa

from app.core.errors import NoContentToExtractError, ProcessingInProgressError
from app.models.enums import DownloadStatus, ExtractionQuality, ExtractionStatus
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.services.pdf_text import PARSER_VERSION
from app.services.tender_document_processing import process_tender_document
from tests.fixtures.pdf_builder import build_pdf

PUBLIC_IP = "93.184.216.34"
PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)
PCAP_V2 = build_pdf(["Plec REVISAT de clausules administratives particulars. " * 8] * 4)


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving(payload: bytes, content_type: str = "application/pdf"):
    """Client fals que sempre respon amb aquests bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def process(session, document, payload=PCAP, **kwargs):
    async with serving(payload) as client:
        return await process_tender_document(
            session, document, client=client, resolver=resolver, **kwargs
        )


async def test_processing_downloads_stores_and_extracts(session, tender_document):
    outcome = await process(session, tender_document)

    assert outcome.reused is False
    file = outcome.file
    assert file.download_status == DownloadStatus.DOWNLOADED
    assert file.version_number == 1
    assert file.is_current is True
    assert file.content is not None
    assert file.content.sha256 == hashlib.sha256(PCAP).hexdigest()
    assert file.content.mime_type == "application/pdf"

    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == file.content_id
        )
    )
    assert extraction.status == ExtractionStatus.EXTRACTED
    assert extraction.parser_version == PARSER_VERSION
    assert extraction.page_count == 3
    assert extraction.quality == ExtractionQuality.GOOD

    pages = (
        await session.scalars(
            sa.select(TenderDocumentPage)
            .where(TenderDocumentPage.extraction_id == extraction.id)
            .order_by(TenderDocumentPage.page_number)
        )
    ).all()
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert "clausules" in pages[0].text


async def test_the_original_bytes_reach_the_storage(session, tender_document, storage):
    outcome = await process(session, tender_document)

    stored = await storage.download(outcome.file.content.storage_key)
    assert stored == PCAP
    assert hashlib.sha256(stored).hexdigest() == outcome.file.content.sha256


async def test_processing_twice_does_not_download_or_parse_again(session, tender_document):
    """Cas A del versionat, i el criteri d'acceptacio 7."""
    first = await process(session, tender_document)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        second = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert calls == [], "no s'havia de tornar a demanar la URL"
    assert second.reused is True
    assert second.file.id == first.file.id

    total_files = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    assert total_files == 1


async def test_force_download_with_identical_bytes_keeps_one_version(session, tender_document):
    """Cas A explicit: es torna a baixar, el hash coincideix, no hi ha versio nova."""
    first = await process(session, tender_document)
    second = await process(session, tender_document, force_download=True)

    assert second.reused is True
    assert second.file.id == first.file.id
    assert second.file.last_checked_at is not None

    total = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    assert total == 1


async def test_changed_content_creates_a_new_version_and_keeps_the_old(session, tender_document):
    """Cas B: cap original processat es perd mai."""
    first = await process(session, tender_document)
    second = await process(session, tender_document, payload=PCAP_V2, force_download=True)

    assert second.file.id != first.file.id
    assert second.file.version_number == 2
    assert second.file.is_current is True

    await session.refresh(first.file)
    assert first.file.is_current is False
    assert first.file.content_id is not None, "l'original antic s'ha de conservar"

    contents = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderDocumentContent)
    )
    assert contents == 2


async def test_the_same_bytes_under_two_documents_are_stored_and_parsed_once(
    session, tender_document, second_tender_document, storage
):
    """Cas C i la deduplicacio del §27: nivell de dedup = els bytes."""
    first = await process(session, tender_document)

    uploads_before = len(storage.uploads)
    second = await process(session, second_tender_document)

    assert second.file.content_id == first.file.content_id
    assert len(storage.uploads) == uploads_before, "no s'havia de tornar a pujar el blob"

    contents = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentContent))
    extractions = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderDocumentExtraction)
    )
    files = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))

    assert (contents, extractions, files) == (1, 1, 2)


async def test_a_new_parser_version_re_extracts_without_re_downloading(
    session, tender_document, monkeypatch
):
    await process(session, tender_document)

    monkeypatch.setattr("app.services.tender_document_processing.PARSER_VERSION", "pypdf-v2")
    monkeypatch.setattr("app.services.pdf_text.PARSER_VERSION", "pypdf-v2")

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        await process_tender_document(session, tender_document, client=client, resolver=resolver)

    assert calls == [], "un canvi de parser no ha de tornar a baixar el PDF"

    versions = (
        await session.scalars(sa.select(TenderDocumentExtraction.parser_version))
    ).all()
    assert sorted(versions) == ["pypdf-v1", "pypdf-v2"], "l'extraccio anterior s'ha de conservar"

    current = await session.scalar(
        sa.select(TenderDocumentExtraction).where(TenderDocumentExtraction.is_current.is_(True))
    )
    assert current.parser_version == "pypdf-v2"


async def test_force_reextract_replaces_the_pages_of_the_same_parser_version(
    session, tender_document
):
    first = await process(session, tender_document)
    await process(session, tender_document, force_reextract=True)

    extractions = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderDocumentExtraction)
    )
    assert extractions == 1, "el mateix parser no genera historia"

    pages = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentPage))
    assert pages == 3, "les pagines s'han de substituir, no duplicar"
    assert first.file.content_id is not None


async def test_force_reextract_without_content_is_refused(session, tender_document):
    with pytest.raises(NoContentToExtractError):
        await process(session, tender_document, force_reextract=True)


async def test_a_failed_download_is_recorded_without_losing_a_good_version(
    session, tender_document
):
    good = await process(session, tender_document)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(failing), follow_redirects=False
    ) as client:
        outcome = await process_tender_document(
            session, tender_document, client=client, resolver=resolver, force_download=True
        )

    assert outcome.file.download_status == DownloadStatus.FAILED
    assert outcome.file.error_code == "http_error"
    assert outcome.file.is_current is False, "una fallada no destrona una versio bona"

    await session.refresh(good.file)
    assert good.file.is_current is True


async def test_a_non_pdf_is_stored_but_not_parsed(session, tender_document):
    xml = b'<?xml version="1.0"?><doc/>'
    async with serving(xml, "text/xml") as client:
        outcome = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    assert outcome.file.content.is_text_extractable is False

    extraction = await session.scalar(sa.select(TenderDocumentExtraction))
    assert extraction.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert extraction.error_code == "unsupported_for_text_extraction"
    assert extraction.quality == ExtractionQuality.NOT_APPLICABLE


async def test_a_second_request_while_one_is_in_flight_gets_a_conflict(session, tender_document):
    """Doble clic: el segon request no duplica la feina."""
    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=False,
            download_status=DownloadStatus.DOWNLOADING,
        )
    )
    await session.commit()

    with pytest.raises(ProcessingInProgressError):
        await process(session, tender_document)


async def test_a_stale_in_flight_row_is_reclaimed(session, tender_document, monkeypatch):
    """Si el proces mor, el document no pot quedar bloquejat per sempre."""
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings

    stale = TenderDocumentFile(
        tender_document_id=tender_document.id,
        source_url=tender_document.url,
        version_number=1,
        is_current=False,
        download_status=DownloadStatus.DOWNLOADING,
        started_at=datetime.now(UTC)
        - timedelta(seconds=settings.DOCUMENT_PROCESSING_STALE_SECONDS + 60),
    )
    session.add(stale)
    await session.commit()

    outcome = await process(session, tender_document)

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    await session.refresh(stale)
    assert stale.download_status == DownloadStatus.FAILED
    assert stale.error_code == "stale"
```

- [ ] **Step 2: Afegeix les fixtures `storage` i `second_tender_document`**

A `apps/api/tests/conftest.py`. La fixture `storage` ha de comptar les pujades perquè el test de deduplicació pugui demostrar que **no** n'hi ha una segona:

```python
class CountingStorage(LocalFilesystemStorage):
    """Storage local que porta el compte de les pujades.

    Serveix per demostrar una absencia: que deduplicar per SHA-256 evita de
    veritat una segona pujada, no nomes una segona fila a la base de dades.
    """

    def __init__(self, root):
        super().__init__(root)
        self.uploads: list[str] = []

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.uploads.append(key)
        await super().upload(key, data, content_type)


@pytest.fixture
def storage(tmp_path):
    backend = CountingStorage(tmp_path / "storage")
    set_storage(backend)
    yield backend
    set_storage(None)


@pytest.fixture
async def second_tender_document(session, tender_document):
    """Un altre TenderDocument, del mateix Tender, amb una URL diferent."""
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument

    document = TenderDocument(
        tender_id=tender_document.tender_id,
        name="PCAP-copia.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/altra-ruta/pcap-copia.pdf",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document
```

Si la fixture d'storage existent ja fa `set_storage`, fes que `storage` la substitueixi i marca-la `autouse=False`. Els tests que no demanin `storage` han de continuar funcionant amb el backend que ja hi havia.

- [ ] **Step 3: Executa els tests i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_tender_document_processing.py -q`
Expected: FAIL amb `ModuleNotFoundError: No module named 'app.services.tender_document_processing'`.

- [ ] **Step 4: Implementa l'orquestrador**

Crea `apps/api/app/services/tender_document_processing.py`:

```python
"""Orquestracio del processat d'un document de licitacio.

L'unic modul de la capa documental que toca la base de dades i l'storage. Els
altres tres (guarda d'URL, descarregador, parser) son purs i es poden provar
sense cap sessio.

Deduplicacio: **el nivell son els bytes**. Els mateixos bytes = un sol objecte a
l'storage i una sola extraccio per `parser_version`, apuntin-hi els
`TenderDocument` que apuntin. El que NO es dedupliqua es la peticio HTTP: per
coneixer el SHA-256 cal baixar-los.

Concurrencia sense Redis ni cua: l'index unic parcial sobre els estats en curs
fa de lock. El segon request rep 409 en comptes de duplicar la feina.

Transaccions: cap peticio HTTP dins d'una transaccio oberta. Es reclama el
document en una transaccio curta, es descarrega i es parseja fora, i es tanca
amb una altra transaccio curta.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NoContentToExtractError, ProcessingInProgressError
from app.models.enums import DownloadStatus, ExtractionStatus
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument
from app.services.document_download import DownloadError, DownloadResult, download_document
from app.services.pdf_text import (
    PARSER_VERSION,
    ExtractionResult,
    extract_pdf_text_async,
    unsupported_result,
)
from app.services.storage import build_tender_document_key, get_storage
from app.services.url_guard import Resolver

logger = logging.getLogger(__name__)

TERMINAL_EXTRACTION_STATES = frozenset(
    {ExtractionStatus.EXTRACTED, ExtractionStatus.SKIPPED_UNSUPPORTED, ExtractionStatus.FAILED}
)


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    file: TenderDocumentFile
    #: Cert si no ha calgut ni baixar ni parsejar res de nou.
    reused: bool


async def current_file_for(
    session: AsyncSession, document_id: uuid.UUID
) -> TenderDocumentFile | None:
    return await session.scalar(
        sa.select(TenderDocumentFile)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.is_current.is_(True),
        )
        .order_by(TenderDocumentFile.version_number.desc())
        .limit(1)
    )


async def current_extraction_for(
    session: AsyncSession, content_id: uuid.UUID
) -> TenderDocumentExtraction | None:
    return await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )


async def _reclaim_stale(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Allibera un processat que ha quedat penjat perque el proces va morir.

    Sense aixo, una caiguda entre el claim i el tancament bloquejaria el
    document per sempre: l'index parcial no deixaria reclamar-lo mai mes.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.DOCUMENT_PROCESSING_STALE_SECONDS)
    result = await session.execute(
        sa.update(TenderDocumentFile)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.download_status.in_(
                [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]
            ),
            TenderDocumentFile.started_at < cutoff,
        )
        .values(
            download_status=DownloadStatus.FAILED,
            error_code="stale",
            error_message="El processat es va interrompre i s'ha alliberat automaticament.",
        )
    )
    if result.rowcount:
        await session.commit()
        logger.warning(
            "document.process.stale_reclaimed",
            extra={"document_id": str(document_id), "rows": result.rowcount},
        )


async def _claim(session: AsyncSession, document: TenderDocument) -> TenderDocumentFile:
    """Reserva el document per a aquest processat. Llanca 409 si ja n'hi ha un en curs."""
    highest = await session.scalar(
        sa.select(sa.func.max(TenderDocumentFile.version_number)).where(
            TenderDocumentFile.tender_document_id == document.id
        )
    )
    file = TenderDocumentFile(
        tender_document_id=document.id,
        source_record_id=document.source_record_id,
        source_url=document.url,
        version_number=(highest or 0) + 1,
        is_current=False,
        download_status=DownloadStatus.DOWNLOADING,
        started_at=datetime.now(UTC),
    )
    session.add(file)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ProcessingInProgressError(
            "Aquest document ja s'esta processant. Torna-ho a provar d'aqui a un moment."
        ) from None
    await session.refresh(file)
    return file


async def _get_or_create_content(
    session: AsyncSession, result: DownloadResult
) -> tuple[TenderDocumentContent, bool]:
    """Retorna el contingut i si s'acaba de crear.

    Aquest es el punt exacte de la deduplicacio: si el SHA-256 ja hi es, ni es
    puja el blob ni es tornara a parsejar.
    """
    existing = await session.scalar(
        sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == result.sha256)
    )
    if existing is not None:
        return existing, False

    key = build_tender_document_key(result.sha256, result.detected_extension)
    await get_storage().upload(key, result.content, result.mime_type)

    content = TenderDocumentContent(
        sha256=result.sha256,
        storage_key=key,
        file_size=result.file_size,
        mime_type=result.mime_type,
        detected_extension=result.detected_extension,
        is_text_extractable=result.mime_type in settings.document_text_extraction_mime_types,
    )
    session.add(content)
    try:
        await session.commit()
    except IntegrityError:
        # Un altre processat ha creat el mateix contingut mentrestant. El blob
        # ja hi es i es identic: nomes cal quedar-se el que ha guanyat.
        await session.rollback()
        winner = await session.scalar(
            sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == result.sha256)
        )
        return winner, False
    await session.refresh(content)
    return content, True


async def _store_extraction(
    session: AsyncSession, content: TenderDocumentContent, result: ExtractionResult
) -> TenderDocumentExtraction:
    """Desa una extraccio i les seves pagines, i la marca com l'actual.

    Si ja existeix una extraccio d'aquesta `parser_version`, se'n substitueixen
    les pagines: el parser es determinista, aixi que repetir-lo nomes te sentit
    per recuperar-se d'un error, no per generar historia.
    """
    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content.id,
            TenderDocumentExtraction.parser_version == result.parser_version,
        )
    )

    # Cap altra extraccio d'aquest contingut pot quedar marcada com a actual.
    await session.execute(
        sa.update(TenderDocumentExtraction)
        .where(
            TenderDocumentExtraction.content_id == content.id,
            TenderDocumentExtraction.is_current.is_(True),
        )
        .values(is_current=False)
    )

    if extraction is None:
        extraction = TenderDocumentExtraction(
            content_id=content.id, parser_version=result.parser_version
        )
        session.add(extraction)
    else:
        await session.execute(
            sa.delete(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id == extraction.id
            )
        )

    extraction.status = result.status
    extraction.quality = result.quality
    extraction.page_count = result.page_count
    extraction.extracted_page_count = result.extracted_page_count
    extraction.empty_page_count = result.empty_page_count
    extraction.text_char_count = result.text_char_count
    extraction.ocr_candidate = result.ocr_candidate
    extraction.parse_ms = result.parse_ms
    extraction.error_code = result.error_code
    extraction.error_message = result.error_message
    extraction.is_current = True
    extraction.completed_at = datetime.now(UTC)
    await session.flush()

    for page in result.pages:
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=page.page_number,
                text=page.text,
                char_count=page.char_count,
                text_hash=page.text_hash,
                status=page.status,
            )
        )

    await session.commit()
    await session.refresh(extraction)

    logger.info(
        "document.extract.done",
        extra={
            "content_id": str(content.id),
            "extraction_id": str(extraction.id),
            "parser_version": result.parser_version,
            "pages": result.page_count,
            # Nomes el recompte: el text dels plecs no es loguega mai.
            "chars": result.text_char_count,
            "quality": str(result.quality),
            "parse_ms": result.parse_ms,
            "error_code": result.error_code,
        },
    )
    return extraction


async def _extract(
    session: AsyncSession, content: TenderDocumentContent
) -> TenderDocumentExtraction:
    """Parseja els bytes ja guardats. No fa cap peticio HTTP."""
    if not content.is_text_extractable:
        return await _store_extraction(session, content, unsupported_result(content.mime_type))

    data = await get_storage().download(content.storage_key)
    return await _store_extraction(session, content, await extract_pdf_text_async(data))


async def _ensure_current_extraction(
    session: AsyncSession, content: TenderDocumentContent, *, force: bool
) -> tuple[TenderDocumentExtraction, bool]:
    """Retorna l'extraccio vigent i si s'ha hagut de fer feina.

    Es reextreu quan es demana explicitament, quan no n'hi ha cap, quan la que
    hi ha no va acabar, o quan la `parser_version` vigent ha canviat.
    """
    current = await current_extraction_for(session, content.id)
    up_to_date = (
        current is not None
        and current.parser_version == PARSER_VERSION
        and current.status in TERMINAL_EXTRACTION_STATES
    )
    if up_to_date and not force:
        return current, False
    return await _extract(session, content), True


async def process_tender_document(
    session: AsyncSession,
    document: TenderDocument,
    *,
    force_download: bool = False,
    force_reextract: bool = False,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> ProcessOutcome:
    """Descarrega, guarda i extreu el text d'un document de licitacio.

    Sense flags es idempotent: si ja hi ha una versio descarregada amb una
    extraccio vigent, no es toca la xarxa.
    """
    await _reclaim_stale(session, document.id)
    current = await current_file_for(session, document.id)
    has_content = current is not None and current.content_id is not None

    # Re-extreure sense re-descarregar: nomes cal si tenim els bytes.
    if force_reextract and not force_download:
        if not has_content:
            raise NoContentToExtractError(
                "Aquest document no s'ha descarregat mai correctament: no hi ha res per re-extreure."
            )
        await _extract(session, current.content)
        await session.refresh(current)
        return ProcessOutcome(file=current, reused=False)

    # Cami idempotent: ja tenim bytes bons i no ens demanen tornar a baixar.
    if has_content and not force_download:
        _, worked = await _ensure_current_extraction(session, current.content, force=False)
        await session.refresh(current)
        return ProcessOutcome(file=current, reused=not worked)

    previous = current if has_content else None
    file = await _claim(session, document)

    try:
        result = await download_document(document.url, client=client, resolver=resolver)
    except DownloadError as exc:
        file.download_status = DownloadStatus.FAILED
        file.is_current = previous is None  # una fallada no destrona una versio bona
        file.error_code = exc.code
        file.error_message = str(exc)[:500]
        file.http_status = exc.http_status
        await session.commit()
        await session.refresh(file)
        logger.warning(
            "document.process.failed",
            extra={
                "document_id": str(document.id),
                "file_id": str(file.id),
                "error_code": exc.code,
                "http_status": exc.http_status,
            },
        )
        return ProcessOutcome(file=file, reused=False)

    # Cas A: els mateixos bytes que la versio vigent. La fila reclamada era
    # provisional i no representa cap versio nova, aixi que s'elimina.
    if previous is not None and previous.content.sha256 == result.sha256:
        await session.delete(file)
        previous.last_checked_at = datetime.now(UTC)
        await session.commit()
        await _ensure_current_extraction(session, previous.content, force=force_reextract)
        await session.refresh(previous)
        return ProcessOutcome(file=previous, reused=not force_reextract)

    content, created = await _get_or_create_content(session, result)

    file.content_id = content.id
    file.download_status = DownloadStatus.DOWNLOADED
    file.http_status = result.http_status
    file.final_url = result.final_url
    file.original_filename = result.original_filename
    file.detected_filename = result.detected_filename
    file.declared_mime_type = result.declared_mime_type
    file.redirect_count = result.redirect_count
    file.download_ms = result.download_ms
    file.downloaded_at = datetime.now(UTC)
    file.last_checked_at = datetime.now(UTC)
    file.is_current = True
    if previous is not None:
        previous.is_current = False
    await session.commit()

    # Cas C: si el contingut ja hi era, aqui no es torna a parsejar res.
    await _ensure_current_extraction(session, content, force=force_reextract)
    await session.refresh(file)

    logger.info(
        "document.process.ok",
        extra={
            "document_id": str(document.id),
            "file_id": str(file.id),
            "content_id": str(content.id),
            "sha256": content.sha256,
            "bytes": content.file_size,
            "download_ms": result.download_ms,
            "new_content": created,
            "version": file.version_number,
        },
    )
    return ProcessOutcome(file=file, reused=False)
```

- [ ] **Step 5: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_tender_document_processing.py -q`
Expected: PASS, 13 tests.

Notes de depuració probables:
- Si `test_a_second_request_while_one_is_in_flight_gets_a_conflict` no llança, comprova que `PRAGMA foreign_keys` i l'índex parcial existeixen a SQLite: `sa.text("download_status IN ('pending', 'downloading')")` ha de coincidir **exactament** amb els valors de l'enum en minúscules.
- Si `test_the_same_bytes_under_two_documents...` compta 2 pujades, revisa que `_get_or_create_content` consulti per `sha256` **abans** de cridar `upload`.

- [ ] **Step 6: Linter i suite completa**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/tender_document_processing.py \
        apps/api/tests/test_tender_document_processing.py apps/api/tests/conftest.py
git commit -m "feat(documents): orquestracio de dedup, versionat i concurrencia

Els tres casos del versionat: mateix hash no crea versio, hash diferent en crea
una de nova conservant l'antiga, i un hash ja conegut reutilitza el blob sense
tornar a pujar-lo ni parsejar-lo. Re-download i re-extract son camins separats.

El lock es l'index unic parcial: doble clic dona 409, i un processat mort es
reclama passat el termini en comptes de bloquejar el document per sempre.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 7: Schemas i endpoints

Vuit endpoints públics globals: sessió iniciada, sense pertinença a cap empresa, com el router `tenders` actual. Cap resposta conté `storage_key`.

**Files:**
- Create: `apps/api/app/schemas/tender_documents.py`
- Create: `apps/api/app/api/v1/routers/tender_documents.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/test_tender_documents_api.py`

**Interfaces:**
- Consumes: `process_tender_document`, `ProcessOutcome`, `current_file_for`, `current_extraction_for` (Task 6); els models (Task 5); `SessionDep`, `CurrentUser` (`app.api.deps`); `Page` i `ORMModel` (`app.schemas.tenders` / `app.schemas.common`)
- Produces: `router` a `app.api.v1.routers.tender_documents`; els schemas `TenderDocumentFileRead`, `TenderDocumentContentRead`, `TenderDocumentExtractionRead`, `TenderDocumentPageRead`, `TenderDocumentProcessResult`, `TenderDocumentProcessRequest`, `TenderDocumentFileSummary`

- [ ] **Step 1: Escriu els tests que fallen**

Crea `apps/api/tests/test_tender_documents_api.py`:

```python
"""Endpoints de la capa documental.

Son dades publiques i globals: cal sessio, pero no pertinenca a cap empresa.
"""

from __future__ import annotations

import httpx
import pytest

from tests.fixtures.pdf_builder import build_pdf

PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch):
    """Tot el router comparteix el mateix origen fals."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    real = None
    import app.api.v1.routers.tender_documents as module
    from app.services import tender_document_processing as processing

    original = processing.download_document

    async def patched(url, *, client=None, resolver=None):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as fake:
            return await original(
                url, client=fake, resolver=lambda host, port: ["93.184.216.34"]
            )

    monkeypatch.setattr(processing, "download_document", patched)
    del real, module


async def test_processing_requires_a_session(client, tender_document):
    response = await client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )
    assert response.status_code == 401


async def test_processes_a_document_and_returns_the_file(auth_client, tender_document):
    response = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reused"] is False
    file = body["file"]
    assert file["download_status"] == "downloaded"
    assert file["version_number"] == 1
    assert file["content"]["mime_type"] == "application/pdf"
    assert file["extraction"]["page_count"] == 3
    assert file["extraction"]["quality"] == "good"


async def test_no_response_ever_leaks_the_storage_key(auth_client, tender_document):
    response = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )
    assert "storage_key" not in response.text


async def test_processing_twice_reports_reuse(auth_client, tender_document):
    base = (
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    await auth_client.post(base)
    second = await auth_client.post(base)

    assert second.json()["reused"] is True


async def test_lists_the_versions_of_a_document(auth_client, tender_document):
    await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )

    response = await auth_client.get(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/files"
    )

    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["is_current"] is True


async def test_reads_the_pages_of_the_current_extraction(auth_client, tender_document):
    processed = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]

    response = await auth_client.get(f"/api/v1/tender-document-files/{file_id}/pages")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [page["page_number"] for page in body["items"]] == [1, 2, 3]
    assert "clausules" in body["items"][0]["text"]


async def test_reads_one_page_by_its_human_number(auth_client, tender_document):
    processed = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]
    extraction_id = processed.json()["file"]["extraction"]["id"]

    response = await auth_client.get(
        f"/api/v1/tender-document-extractions/{extraction_id}/pages/2"
    )

    assert response.status_code == 200
    assert response.json()["page_number"] == 2

    missing = await auth_client.get(
        f"/api/v1/tender-document-extractions/{extraction_id}/pages/99"
    )
    assert missing.status_code == 404

    # La pagina 0 no existeix: la numeracio comenca a 1 arreu.
    zero = await auth_client.get(f"/api/v1/tender-document-extractions/{extraction_id}/pages/0")
    assert zero.status_code == 422
    assert file_id


async def test_serves_the_original_pdf_inline(auth_client, tender_document):
    processed = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]

    response = await auth_client.get(f"/api/v1/tender-document-files/{file_id}/original")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content == PCAP


async def test_unknown_identifiers_are_404(auth_client, tender_document):
    import uuid

    missing = uuid.uuid4()
    assert (await auth_client.get(f"/api/v1/tender-document-files/{missing}")).status_code == 404
    assert (
        await auth_client.post(
            f"/api/v1/tenders/{tender_document.tender_id}/documents/{missing}/process"
        )
    ).status_code == 404


async def test_a_document_of_another_tender_is_not_reachable(auth_client, tender_document):
    """El tender_id de la ruta ha de coincidir de veritat amb el del document."""
    import uuid

    response = await auth_client.post(
        f"/api/v1/tenders/{uuid.uuid4()}/documents/{tender_document.id}/process"
    )
    assert response.status_code == 404


async def test_reextract_without_content_is_a_422(auth_client, tender_document):
    response = await auth_client.post(
        f"/api/v1/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/process",
        json={"force_reextract": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_content_to_extract"
```

Aquest fitxer fa servir les fixtures `client` i `auth_client` que ja existeixen a `conftest.py`. Comprova'n els noms reals abans d'executar-lo i ajusta'ls si difereixen.

- [ ] **Step 2: Executa'ls i verifica que fallen**

Run: `cd apps/api && python -m pytest tests/test_tender_documents_api.py -q`
Expected: FAIL amb 404 a totes les rutes (el router encara no existeix).

- [ ] **Step 3: Escriu els schemas**

Crea `apps/api/app/schemas/tender_documents.py`:

```python
"""Schemas de sortida de la capa documental.

Regla dura: `storage_key` NO apareix enlloc. La clau interna de l'storage es
un detall d'implementacio, i exposar-la convidaria a saltar-se l'endpoint
autoritzat que serveix l'original.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TenderDocumentContentRead(ORMModel):
    id: uuid.UUID
    sha256: str
    file_size: int
    mime_type: str
    detected_extension: str | None = None
    is_text_extractable: bool


class TenderDocumentExtractionRead(ORMModel):
    id: uuid.UUID
    parser_version: str
    status: ExtractionStatus
    quality: ExtractionQuality
    page_count: int
    extracted_page_count: int
    empty_page_count: int
    text_char_count: int
    #: Indici de possible escaneig, no una prova que calgui OCR.
    ocr_candidate: bool
    is_current: bool
    parse_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None


class TenderDocumentPageRead(ORMModel):
    id: uuid.UUID
    #: 1-based, igual que a la base de dades i a la interficie.
    page_number: int
    text: str
    char_count: int
    text_hash: str
    status: PageExtractionStatus


class TenderDocumentFileRead(ORMModel):
    id: uuid.UUID
    tender_document_id: uuid.UUID
    version_number: int
    is_current: bool
    download_status: DownloadStatus
    http_status: int | None = None
    source_url: str
    final_url: str | None = None
    original_filename: str | None = None
    detected_filename: str | None = None
    declared_mime_type: str | None = None
    redirect_count: int
    download_ms: int | None = None
    downloaded_at: datetime | None = None
    last_checked_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    content: TenderDocumentContentRead | None = None
    #: L'extraccio vigent del contingut, si n'hi ha.
    extraction: TenderDocumentExtractionRead | None = None


class TenderDocumentProcessRequest(BaseModel):
    #: Torna a demanar la URL i aplica els casos A/B/C del versionat.
    force_download: bool = False
    #: Re-parseja els bytes ja guardats, sense tornar a baixar.
    force_reextract: bool = False


class TenderDocumentProcessResult(BaseModel):
    #: Cert si no ha calgut ni baixar ni parsejar res.
    reused: bool
    file: TenderDocumentFileRead
```

- [ ] **Step 4: Escriu el router**

Crea `apps/api/app/api/v1/routers/tender_documents.py`:

```python
"""Processat i lectura de plecs.

Com el feed de licitacions, aquests endpoints NO pengen de
`/companies/{company_id}`: els plecs son publics i globals. Cal sessio
iniciada, no pertinenca a cap empresa.

`POST .../process` es sincron i retorna el resultat final. Una descarrega
fallida NO es un error de la peticio: la peticio ha fet la seva feina i n'ha
registrat el resultat, aixi que respon 200 amb `download_status: failed` i el
seu `error_code`, i la interficie ho pot mostrar a la fila del document.
"""

from __future__ import annotations

import io
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument
from app.schemas.tender_documents import (
    TenderDocumentExtractionRead,
    TenderDocumentFileRead,
    TenderDocumentPageRead,
    TenderDocumentProcessRequest,
    TenderDocumentProcessResult,
)
from app.schemas.tenders import Page
from app.services.storage import get_storage
from app.services.tender_document_processing import (
    current_extraction_for,
    process_tender_document,
)

router = APIRouter(tags=["plecs"])


async def _load_document(
    session: SessionDep, tender_id: uuid.UUID, document_id: uuid.UUID
) -> TenderDocument:
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id,
            # El tender de la ruta ha de coincidir: sense aixo, la URL
            # afirmaria una relacio que no s'ha comprovat.
            TenderDocument.tender_id == tender_id,
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    return document


async def _load_file(session: SessionDep, file_id: uuid.UUID) -> TenderDocumentFile:
    file = await session.scalar(
        sa.select(TenderDocumentFile).where(TenderDocumentFile.id == file_id)
    )
    if file is None:
        raise NotFoundError("Fitxer no trobat.")
    return file


async def _load_extraction(
    session: SessionDep, extraction_id: uuid.UUID
) -> TenderDocumentExtraction:
    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(TenderDocumentExtraction.id == extraction_id)
    )
    if extraction is None:
        raise NotFoundError("Extraccio no trobada.")
    return extraction


async def _file_read(session: SessionDep, file: TenderDocumentFile) -> TenderDocumentFileRead:
    """Munta la resposta afegint-hi l'extraccio vigent del contingut."""
    read = TenderDocumentFileRead.model_validate(file)
    if file.content_id is not None:
        extraction = await current_extraction_for(session, file.content_id)
        if extraction is not None:
            read.extraction = TenderDocumentExtractionRead.model_validate(extraction)
    return read


@router.post(
    "/tenders/{tender_id}/documents/{document_id}/process",
    response_model=TenderDocumentProcessResult,
    summary="Processa un document de licitacio",
)
async def process_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    payload: TenderDocumentProcessRequest | None = None,
):
    document = await _load_document(session, tender_id, document_id)
    options = payload or TenderDocumentProcessRequest()
    outcome = await process_tender_document(
        session,
        document,
        force_download=options.force_download,
        force_reextract=options.force_reextract,
    )
    return TenderDocumentProcessResult(
        reused=outcome.reused, file=await _file_read(session, outcome.file)
    )


@router.get(
    "/tenders/{tender_id}/documents/{document_id}/files",
    response_model=list[TenderDocumentFileRead],
    summary="Versions descarregades d'un document",
)
async def list_document_files(
    tender_id: uuid.UUID, document_id: uuid.UUID, session: SessionDep, _: CurrentUser
):
    await _load_document(session, tender_id, document_id)
    files = (
        await session.scalars(
            sa.select(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == document_id)
            .order_by(TenderDocumentFile.version_number.desc())
        )
    ).all()
    return [await _file_read(session, file) for file in files]


@router.get(
    "/tender-document-files/{file_id}",
    response_model=TenderDocumentFileRead,
    summary="Detall d'una versio",
)
async def get_file(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    return await _file_read(session, await _load_file(session, file_id))


@router.get(
    "/tender-document-files/{file_id}/extractions",
    response_model=list[TenderDocumentExtractionRead],
    summary="Extraccions fetes sobre aquests bytes",
)
async def list_file_extractions(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    file = await _load_file(session, file_id)
    if file.content_id is None:
        return []
    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == file.content_id)
            .order_by(TenderDocumentExtraction.created_at.desc())
        )
    ).all()
    return [TenderDocumentExtractionRead.model_validate(item) for item in extractions]


async def _paginated_pages(
    session: SessionDep, extraction_id: uuid.UUID, limit: int, offset: int
) -> Page[TenderDocumentPageRead]:
    total = await session.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocumentPage)
        .where(TenderDocumentPage.extraction_id == extraction_id)
    )
    rows = (
        await session.scalars(
            sa.select(TenderDocumentPage)
            .where(TenderDocumentPage.extraction_id == extraction_id)
            .order_by(TenderDocumentPage.page_number)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[TenderDocumentPageRead.model_validate(row) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tender-document-files/{file_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines de l'extraccio vigent",
)
async def list_file_pages(
    file_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    file = await _load_file(session, file_id)
    extraction = (
        await current_extraction_for(session, file.content_id) if file.content_id else None
    )
    if extraction is None:
        return Page(items=[], total=0, limit=limit, offset=offset)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/tender-document-extractions/{extraction_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines d'una extraccio concreta",
)
async def list_extraction_pages(
    extraction_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    extraction = await _load_extraction(session, extraction_id)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/tender-document-extractions/{extraction_id}/pages/{page_number}",
    response_model=TenderDocumentPageRead,
    summary="Una pagina concreta",
)
async def get_extraction_page(
    extraction_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    # ge=1 perque la numeracio es 1-based: la pagina 0 no existeix.
    page_number: int = Path(ge=1),
):
    await _load_extraction(session, extraction_id)
    page = await session.scalar(
        sa.select(TenderDocumentPage).where(
            TenderDocumentPage.extraction_id == extraction_id,
            TenderDocumentPage.page_number == page_number,
        )
    )
    if page is None:
        raise NotFoundError(f"La pagina {page_number} no existeix en aquesta extraccio.")
    return TenderDocumentPageRead.model_validate(page)


@router.get("/tender-document-files/{file_id}/original", summary="Descarrega l'original")
async def download_original(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    file = await _load_file(session, file_id)
    if file.content is None:
        raise NotFoundError("Aquesta versio no te cap fitxer descarregat.")

    data = await get_storage().download(file.content.storage_key)
    filename = (
        file.detected_filename
        or file.original_filename
        or f"{file.content.sha256[:16]}.{file.content.detected_extension or 'bin'}"
    )
    # Inline nomes per a PDF: es el que el navegador sap mostrar sense baixar-lo.
    disposition = "inline" if file.content.mime_type == "application/pdf" else "attachment"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=file.content.mime_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(file.content.file_size),
        },
    )
```

- [ ] **Step 5: Registra el router**

A `apps/api/app/api/v1/router.py`, afegeix `tender_documents` a la llista d'imports de `app.api.v1.routers` (ordre alfabètic, després de `tenders`) i, just després de `api_router.include_router(tenders.router)`:

```python
# Plecs: tambe publics i globals. El processat es manual des de la fitxa.
api_router.include_router(tender_documents.router)
```

- [ ] **Step 6: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_tender_documents_api.py -q`
Expected: PASS.

Si `test_reextract_without_content_is_a_422` retorna 500, comprova que `NoContentToExtractError` hereta de `ValidationError` i que `register_exception_handlers` la captura via `AppError`.

- [ ] **Step 7: Linter i suite completa**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/schemas/tender_documents.py \
        apps/api/app/api/v1/routers/tender_documents.py apps/api/app/api/v1/router.py \
        apps/api/tests/test_tender_documents_api.py
git commit -m "feat(documents): endpoints de processat, pagines i original

Vuit endpoints publics globals. El processat es sincron i una descarrega
fallida respon 200 amb el seu error_code, perque la peticio si que ha fet la
seva feina: registrar el resultat. Cap resposta exposa storage_key i l'original
es serveix per l'API, no per MinIO.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---

## Task 8: Resum de l'estat documental a la fitxa de licitació

Perquè la llista de documents pugui mostrar l'estat sense fer una crida per fila.

**Files:**
- Modify: `apps/api/app/schemas/tenders.py:85-92`
- Modify: `apps/api/app/api/v1/routers/tenders.py:179-262`
- Test: `apps/api/tests/test_tenders_api.py`

**Interfaces:**
- Consumes: `TenderDocumentFile`, `TenderDocumentContent`, `TenderDocumentExtraction` (Task 5)
- Produces: `app.schemas.tenders.TenderDocumentFileSummary`; camp `latest_file: TenderDocumentFileSummary | None` a `TenderDocumentRead`

- [ ] **Step 1: Escriu el test que falla**

Afegeix a `apps/api/tests/test_tenders_api.py`:

```python
async def test_the_tender_detail_reports_the_documental_state(auth_client, tender_document):
    """Sense processar, un document no te cap fitxer: el camp ha de ser null."""
    response = await auth_client.get(f"/api/v1/tenders/{tender_document.tender_id}")

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 1
    assert documents[0]["latest_file"] is None


async def test_the_tender_detail_summarises_a_processed_document(auth_client, tender_document):
    import httpx

    from app.services import tender_document_processing as processing
    from tests.fixtures.pdf_builder import build_pdf

    pdf = build_pdf(["Plec de clausules administratives particulars. " * 8] * 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as fake:
        original = processing.download_document

        async def patched(url, *, client=None, resolver=None):
            return await original(url, client=fake, resolver=lambda h, p: ["93.184.216.34"])

        processing.download_document = patched
        try:
            await auth_client.post(
                f"/api/v1/tenders/{tender_document.tender_id}"
                f"/documents/{tender_document.id}/process"
            )
        finally:
            processing.download_document = original

    response = await auth_client.get(f"/api/v1/tenders/{tender_document.tender_id}")
    summary = response.json()["documents"][0]["latest_file"]

    assert summary["download_status"] == "downloaded"
    assert summary["extraction_status"] == "extracted"
    assert summary["page_count"] == 2
    assert summary["quality"] == "good"
    assert summary["ocr_candidate"] is False
    assert "storage_key" not in response.text
```

- [ ] **Step 2: Executa i verifica que falla**

Run: `cd apps/api && python -m pytest tests/test_tenders_api.py -q -k documental`
Expected: FAIL amb `KeyError: 'latest_file'`.

- [ ] **Step 3: Afegeix el schema de resum**

A `apps/api/app/schemas/tenders.py`, just abans de `class TenderDocumentRead`:

```python
class TenderDocumentFileSummary(ORMModel):
    """Estat documental d'un document, prou per pintar una fila de la llista.

    Es planer expressament: la fitxa no ha de fer una crida per document per
    saber si un plec ja s'ha processat.
    """

    file_id: uuid.UUID
    version_number: int
    download_status: DownloadStatus
    file_size: int | None = None
    sha256: str | None = None
    extraction_status: ExtractionStatus | None = None
    quality: ExtractionQuality | None = None
    page_count: int | None = None
    text_char_count: int | None = None
    ocr_candidate: bool = False
    extraction_id: uuid.UUID | None = None
    processed_at: datetime | None = None
    error_code: str | None = None
```

I dins de `TenderDocumentRead`, com a últim camp:

```python
    #: Versio actual descarregada, si n'hi ha cap. `None` = mai processat.
    latest_file: TenderDocumentFileSummary | None = None
```

Afegeix als imports del fitxer `DownloadStatus`, `ExtractionStatus` i `ExtractionQuality` des de `app.models.enums`, i `datetime` si encara no hi és.

- [ ] **Step 4: Carrega el resum a la fitxa**

A `apps/api/app/api/v1/routers/tenders.py`, afegeix aquesta funció just abans de `get_tender`:

```python
async def _document_summaries(
    session, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TenderDocumentFileSummary]:
    """Resum de l'estat documental de cada document, en una sola consulta.

    Nomes la versio vigent: l'historial de versions es demana explicitament a
    `/documents/{id}/files`.
    """
    if not document_ids:
        return {}

    rows = await session.execute(
        sa.select(TenderDocumentFile, TenderDocumentContent, TenderDocumentExtraction)
        .outerjoin(
            TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id
        )
        .outerjoin(
            TenderDocumentExtraction,
            sa.and_(
                TenderDocumentExtraction.content_id == TenderDocumentFile.content_id,
                TenderDocumentExtraction.is_current.is_(True),
            ),
        )
        .where(
            TenderDocumentFile.tender_document_id.in_(document_ids),
            TenderDocumentFile.is_current.is_(True),
        )
    )

    summaries: dict[uuid.UUID, TenderDocumentFileSummary] = {}
    for file, content, extraction in rows:
        summaries[file.tender_document_id] = TenderDocumentFileSummary(
            file_id=file.id,
            version_number=file.version_number,
            download_status=file.download_status,
            file_size=content.file_size if content else None,
            sha256=content.sha256 if content else None,
            extraction_status=extraction.status if extraction else None,
            quality=extraction.quality if extraction else None,
            page_count=extraction.page_count if extraction else None,
            text_char_count=extraction.text_char_count if extraction else None,
            ocr_candidate=bool(extraction.ocr_candidate) if extraction else False,
            extraction_id=extraction.id if extraction else None,
            processed_at=file.downloaded_at,
            error_code=file.error_code or (extraction.error_code if extraction else None),
        )
    return summaries
```

Dins de `get_tender`, just abans del `return TenderDetail(...)`:

```python
    summaries = await _document_summaries(session, [doc.id for doc in tender.documents])
```

I substitueix la línia `documents=[...]` per:

```python
        documents=[
            TenderDocumentRead.model_validate(document).model_copy(
                update={"latest_file": summaries.get(document.id)}
            )
            for document in tender.documents
        ],
```

Afegeix els imports que calguin al capdamunt del fitxer: `TenderDocumentFile`, `TenderDocumentContent` i `TenderDocumentExtraction` des de `app.models.tender_documents`, i `TenderDocumentFileSummary` des de `app.schemas.tenders`.

- [ ] **Step 5: Executa els tests**

Run: `cd apps/api && python -m pytest tests/test_tenders_api.py -q`
Expected: PASS.

- [ ] **Step 6: Linter i suite completa**

Run: `cd apps/api && python -m ruff check app tests && python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/tenders.py apps/api/app/api/v1/routers/tenders.py \
        apps/api/tests/test_tenders_api.py
git commit -m "feat(documents): estat documental resumit a la fitxa de licitacio

Una sola consulta amb outer joins porta l'estat de la versio vigent de cada
document, aixi la llista no ha de fer una crida per fila.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 9: Tipus del frontend i secció Documents amb `[Processa]`

**Files:**
- Modify: `apps/web/src/lib/tenders.ts`
- Modify: `apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx:111-124, 300-340`

**Interfaces:**
- Consumes: `TenderDocumentFileSummary` de l'API (Task 8), `POST /tenders/{id}/documents/{id}/process` (Task 7)
- Produces: `TenderDocumentFileSummary`, `TenderDocumentMeta.latest_file`, `DOCUMENT_STATE_LABELS`, `documentState()`

- [ ] **Step 1: Afegeix els tipus i l'etiquetatge d'estat**

A `apps/web/src/lib/tenders.ts`, just abans de `export interface TenderDocumentMeta`:

```ts
export type DownloadStatus = "pending" | "downloading" | "downloaded" | "failed";

export type ExtractionStatus =
  | "pending"
  | "extracting"
  | "extracted"
  | "skipped_unsupported"
  | "failed";

export type ExtractionQuality = "good" | "low_text" | "empty" | "failed" | "not_applicable";

export interface TenderDocumentFileSummary {
  file_id: string;
  version_number: number;
  download_status: DownloadStatus;
  file_size: number | null;
  sha256: string | null;
  extraction_status: ExtractionStatus | null;
  quality: ExtractionQuality | null;
  page_count: number | null;
  text_char_count: number | null;
  ocr_candidate: boolean;
  extraction_id: string | null;
  processed_at: string | null;
  error_code: string | null;
}
```

Afegeix el camp a `TenderDocumentMeta`:

```ts
  /** Versió vigent descarregada. `null` = mai processat. */
  latest_file: TenderDocumentFileSummary | null;
}
```

I al final del fitxer, l'estat derivat que pinta la interfície:

```ts
export type DocumentState =
  | "not_downloaded"
  | "downloaded"
  | "extracted"
  | "ocr_candidate"
  | "unsupported"
  | "error";

/**
 * Estat visible d'un document. Es deriva al frontend perquè és pura
 * presentació: el backend ja publica els estats crus i no li'n cal cap de
 * combinat.
 */
export function documentState(file: TenderDocumentFileSummary | null): DocumentState {
  if (!file) return "not_downloaded";
  if (file.download_status === "failed") return "error";
  if (file.download_status !== "downloaded") return "not_downloaded";
  if (file.extraction_status === "failed") return "error";
  if (file.extraction_status === "skipped_unsupported") return "unsupported";
  if (file.extraction_status !== "extracted") return "downloaded";
  return file.ocr_candidate ? "ocr_candidate" : "extracted";
}

export const DOCUMENT_STATE_LABELS: Record<DocumentState, string> = {
  not_downloaded: "No descarregat",
  downloaded: "Descarregat",
  extracted: "Text extret",
  ocr_candidate: "Poc text · possible escaneig",
  unsupported: "Sense extracció de text",
  error: "Error",
};

export const DOCUMENT_STATE_TONES: Record<DocumentState, "neutral" | "success" | "warning" | "danger"> = {
  not_downloaded: "neutral",
  downloaded: "neutral",
  extracted: "success",
  ocr_candidate: "warning",
  unsupported: "neutral",
  error: "danger",
};

/** Mida llegible. `null` quan encara no s'ha descarregat res. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
```

Comprova els valors reals de `BadgeTone` a `apps/web/src/components/ui.tsx` i ajusta `DOCUMENT_STATE_TONES` perquè hi encaixin. Si el component no té `danger` o `warning`, fes servir els tons que sí que existeixin.

- [ ] **Step 2: Substitueix la secció Documents de la fitxa**

A `apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx`, reemplaça tot el bloc `{/* ---- Documents --- */}` (des de `<Card>` fins al `</Card>` que el tanca) per:

```tsx
        {/* --------------------------------------------------- Documents --- */}
        <Card>
          <CardHeader
            title={`Documents (${data.documents.length})`}
            description="Processa un plec per descarregar-ne l'original i extreure'n el text per pàgina."
            action={
              selected.size > 0 ? (
                <Button onClick={() => void processSelected()} disabled={processing !== null}>
                  Processa {selected.size} document{selected.size > 1 ? "s" : ""}
                </Button>
              ) : null
            }
          />
          {processError && <ErrorNotice message={processError} />}
          {data.documents.length === 0 ? (
            <EmptyState title="Sense documents publicats" />
          ) : (
            <ul className="divide-y divide-border">
              {data.documents.map((document) => {
                const file = document.latest_file;
                const state = documentState(file);
                const busy = processing === document.id;
                return (
                  <li key={document.id} className="flex items-center gap-4 px-5 py-3">
                    <Checkbox
                      checked={selected.has(document.id)}
                      onChange={() => toggleSelected(document.id)}
                      aria-label={`Selecciona ${document.name}`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{document.name}</p>
                      <p className="text-[12px] text-muted">
                        {TENDER_DOCUMENT_LABELS[document.document_type]}
                        {" · "}
                        {formatBytes(file?.file_size ?? null)}
                        {file?.page_count ? ` · ${file.page_count} pàgines` : ""}
                        {file?.processed_at ? ` · ${formatDate(file.processed_at)}` : ""}
                      </p>
                      {file?.error_code && (
                        <p className="text-[12px] text-danger">Error: {file.error_code}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <Badge tone={DOCUMENT_STATE_TONES[state]}>
                        {DOCUMENT_STATE_LABELS[state]}
                      </Badge>
                      {file?.extraction_id && (
                        <Link
                          href={`/licitacions/${data.id}/documents/${document.id}`}
                          className="text-[13px] text-brand hover:underline"
                        >
                          Text
                        </Link>
                      )}
                      <a
                        href={document.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[13px] text-brand hover:underline"
                      >
                        Origen ↗
                      </a>
                      <Button
                        variant="secondary"
                        onClick={() => void processOne(document.id)}
                        disabled={processing !== null}
                      >
                        {busy ? <Spinner /> : file ? "Reprocessa" : "Processa"}
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
```

- [ ] **Step 3: Afegeix l'estat i les accions al component**

A la mateixa pàgina, dins del component, després dels `useState` existents:

```tsx
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [processing, setProcessing] = useState<string | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);

  function toggleSelected(documentId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }

  async function processOne(documentId: string) {
    if (!data) return;
    setProcessing(documentId);
    setProcessError(null);
    try {
      await api.post(`/api/v1/tenders/${data.id}/documents/${documentId}/process`, {});
      await reload();
    } catch (caught) {
      setProcessError(caught instanceof Error ? caught.message : "No s'ha pogut processar.");
    } finally {
      setProcessing(null);
    }
  }

  // Seqüencial i no en paral·lel: descarregar plecs de PLACSP en ràfega seria
  // poc educat amb un servei públic, i el backend és síncron.
  async function processSelected() {
    for (const documentId of selected) {
      await processOne(documentId);
    }
    setSelected(new Set());
  }
```

Afegeix als imports del fitxer: `useState` de `react`, `Link` de `next/link`, `api` de `@/lib/api`, i `Button`, `Checkbox`, `Spinner`, `ErrorNotice` de `@/components/ui`; i de `@/lib/tenders`: `DOCUMENT_STATE_LABELS`, `DOCUMENT_STATE_TONES`, `documentState`, `formatBytes`. Comprova el nom real de la funció de recàrrega que retorna el hook de dades (`reload`) i el prefix real de les crides (`/api/v1/...` o `/api/...`) mirant una crida existent al mateix fitxer.

- [ ] **Step 4: Comprova tipus i lint**

Run: `cd apps/web && npx tsc --noEmit && npx eslint src`
Expected: cap error. Si `CardHeader` no accepta `action`, mou el botó de selecció just a sota de la capçalera dins del `<Card>`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/tenders.ts "apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx"
git commit -m "feat(web): estat documental i boto Processa a la fitxa de licitacio

Estat, mida, pagines i data per document, amb processat individual o de la
seleccio. La seleccio es processa en serie: descarregar plecs de PLACSP en
rafega seria poc educat amb un servei public.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---

## Task 10: Vista del text extret amb navegació per pàgina

**Files:**
- Create: `apps/web/src/app/(app)/licitacions/[tenderId]/documents/[documentId]/page.tsx`

**Interfaces:**
- Consumes: `GET /tenders/{id}/documents/{id}/files`, `GET /tender-document-extractions/{id}/pages/{n}` (Task 7); els tipus de la Task 9

- [ ] **Step 1: Escriu la pàgina**

Crea `apps/web/src/app/(app)/licitacions/[tenderId]/documents/[documentId]/page.tsx`:

```tsx
"use client";

/**
 * Text extret d'un plec, pàgina a pàgina.
 *
 * No és un lector de PDF: és la comprovació visual que la pàgina 27 desada és
 * de veritat la pàgina 27 del document. Abans de connectar-hi cap IA, això és
 * el que cal poder verificar a ull.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Input,
  LoadingRows,
  PageHeader,
} from "@/components/ui";
import { useResource } from "@/lib/hooks";
import {
  DOCUMENT_STATE_LABELS,
  DOCUMENT_STATE_TONES,
  type TenderDocumentFileSummary,
  documentState,
  formatBytes,
} from "@/lib/tenders";

interface FileRead {
  id: string;
  version_number: number;
  is_current: boolean;
  download_status: string;
  source_url: string;
  original_filename: string | null;
  downloaded_at: string | null;
  error_code: string | null;
  content: { sha256: string; file_size: number; mime_type: string } | null;
  extraction: {
    id: string;
    parser_version: string;
    status: string;
    quality: string;
    page_count: number;
    text_char_count: number;
    ocr_candidate: boolean;
    error_code: string | null;
  } | null;
}

interface PageRead {
  page_number: number;
  text: string;
  char_count: number;
  status: string;
}

export default function DocumentTextPage() {
  const params = useParams<{ tenderId: string; documentId: string }>();
  const { data: files, loading, error } = useResource<FileRead[]>(
    `/api/v1/tenders/${params.tenderId}/documents/${params.documentId}/files`,
  );
  const [pageNumber, setPageNumber] = useState(1);

  const current = files?.find((file) => file.is_current) ?? files?.[0] ?? null;
  const extraction = current?.extraction ?? null;

  const { data: page, loading: pageLoading } = useResource<PageRead>(
    extraction ? `/api/v1/tender-document-extractions/${extraction.id}/pages/${pageNumber}` : null,
  );

  const total = extraction?.page_count ?? 0;

  // El resum reutilitza l'etiquetatge de la fitxa perquè els dos llocs han de
  // dir exactament el mateix del mateix document.
  const summary: TenderDocumentFileSummary | null = current
    ? {
        file_id: current.id,
        version_number: current.version_number,
        download_status: current.download_status as never,
        file_size: current.content?.file_size ?? null,
        sha256: current.content?.sha256 ?? null,
        extraction_status: (extraction?.status ?? null) as never,
        quality: (extraction?.quality ?? null) as never,
        page_count: extraction?.page_count ?? null,
        text_char_count: extraction?.text_char_count ?? null,
        ocr_candidate: extraction?.ocr_candidate ?? false,
        extraction_id: extraction?.id ?? null,
        processed_at: current.downloaded_at,
        error_code: current.error_code ?? extraction?.error_code ?? null,
      }
    : null;

  if (loading) return <LoadingRows />;
  if (error) return <ErrorNotice message={error} />;
  if (!current) return <EmptyState title="Aquest document encara no s'ha processat" />;

  return (
    <div className="space-y-4">
      <PageHeader
        title={current.original_filename ?? "Document"}
        description={
          <Link href={`/licitacions/${params.tenderId}`} className="text-brand hover:underline">
            ← Torna a la licitació
          </Link>
        }
      />

      <Card>
        <CardHeader title="Versió actual" />
        <dl className="grid grid-cols-2 gap-3 px-5 py-4 text-[13px] md:grid-cols-4">
          <div>
            <dt className="text-muted">Estat</dt>
            <dd>
              <Badge tone={DOCUMENT_STATE_TONES[documentState(summary)]}>
                {DOCUMENT_STATE_LABELS[documentState(summary)]}
              </Badge>
            </dd>
          </div>
          <div>
            <dt className="text-muted">Versió</dt>
            <dd>
              v{current.version_number} de {files?.length ?? 1}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Mida</dt>
            <dd>{formatBytes(current.content?.file_size ?? null)}</dd>
          </div>
          <div>
            <dt className="text-muted">Pàgines</dt>
            <dd>{total || "—"}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-muted">SHA-256</dt>
            <dd className="truncate font-mono text-[11px]">{current.content?.sha256 ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Parser</dt>
            <dd>{extraction?.parser_version ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Original</dt>
            <dd>
              <a
                href={`/bff/api/v1/tender-document-files/${current.id}/original`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand hover:underline"
              >
                Obre ↗
              </a>
            </dd>
          </div>
        </dl>
        {extraction?.ocr_candidate && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-muted">
            S&apos;ha extret molt poc text. Podria ser un document escanejat. No s&apos;hi ha
            aplicat cap OCR.
          </p>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Text extret"
          description={`Pàgina ${pageNumber} de ${total || "?"} · numeració començant per 1`}
        />
        {total === 0 ? (
          <EmptyState title="Aquest document no té text extret" />
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-border px-5 py-3">
              <Button
                variant="secondary"
                onClick={() => setPageNumber((n) => Math.max(1, n - 1))}
                disabled={pageNumber <= 1}
              >
                Anterior
              </Button>
              <Input
                type="number"
                min={1}
                max={total}
                value={pageNumber}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  if (value >= 1 && value <= total) setPageNumber(value);
                }}
                className="w-20"
                aria-label="Número de pàgina"
              />
              <Button
                variant="secondary"
                onClick={() => setPageNumber((n) => Math.min(total, n + 1))}
                disabled={pageNumber >= total}
              >
                Següent
              </Button>
            </div>
            {pageLoading ? (
              <LoadingRows />
            ) : (
              <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap px-5 py-4 font-mono text-[12px] leading-relaxed text-ink">
                {page?.text || "(pàgina sense text extret)"}
              </pre>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Comprova tipus i lint**

Run: `cd apps/web && npx tsc --noEmit && npx eslint src`
Expected: cap error. Ajusta les props de `PageHeader` i `Input` si les signatures reals difereixen (`PageHeader` pot voler `description: string`, en aquest cas mou l'enllaç a fora).

- [ ] **Step 3: Build**

Run: `cd apps/web && npm run build`
Expected: build correcte, amb la ruta nova a la llista.

- [ ] **Step 4: Commit**

```bash
git add "apps/web/src/app/(app)/licitacions/[tenderId]/documents/[documentId]/page.tsx"
git commit -m "feat(web): vista del text extret amb navegacio per pagina

No es un lector de PDF: es la comprovacio visual que la pagina 27 desada es la
pagina 27 del document. Abans de connectar-hi cap IA, aixo es el que cal poder
verificar a ull.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---
## Task 11: Script de prova real i documentació

**Files:**
- Create: `scripts/process_real_documents.py`
- Create: `docs/documents.md`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `process_tender_document` (Task 6), els models (Task 5)
- Produces: `scripts/process_real_documents.py` executable amb `--limit N` i `--dry-run`

- [ ] **Step 1: Escriu l'script**

Crea `scripts/process_real_documents.py`:

```python
"""Processa uns quants documents reals de PLACSP i n'informa.

Deliberadament acotat: NO baixa els 1.454 documents coneguts. Tria uns quants
tenders amb PDF i en processa un nombre limitat, perque el que volem comprovar
es que la cadena funciona amb PDF de veritat, no fer-nos una copia de PLACSP.

    python scripts/process_real_documents.py --limit 5
    python scripts/process_real_documents.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

import sqlalchemy as sa  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402
from app.models.tender_documents import TenderDocumentExtraction  # noqa: E402
from app.models.tenders import Tender, TenderDocument  # noqa: E402
from app.services.tender_document_processing import process_tender_document  # noqa: E402

# Limit de seguretat absolut: encara que algu passi --limit 500, no.
HARD_LIMIT = 20


async def pick_documents(session, limit: int) -> list[tuple[Tender, TenderDocument]]:
    """Tria documents que semblin PDF, d'expedients diferents.

    Un document per expedient: volem varietat d'organs i de generadors de PDF,
    no cinc annexos del mateix plec.
    """
    rows = (
        await session.execute(
            sa.select(Tender, TenderDocument)
            .join(TenderDocument, TenderDocument.tender_id == Tender.id)
            .where(
                sa.or_(
                    TenderDocument.url.ilike("%.pdf"),
                    TenderDocument.mime_type == "application/pdf",
                )
            )
            .order_by(Tender.publication_date.desc().nullslast())
            .limit(limit * 10)
        )
    ).all()

    picked: list[tuple[Tender, TenderDocument]] = []
    seen: set = set()
    for tender, document in rows:
        if tender.id in seen:
            continue
        seen.add(tender.id)
        picked.append((tender, document))
        if len(picked) >= limit:
            break
    return picked


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="documents a processar (max 20)")
    parser.add_argument("--dry-run", action="store_true", help="nomes llista, no descarrega")
    args = parser.parse_args()
    limit = min(args.limit, HARD_LIMIT)

    async with async_session_maker() as session:
        selection = await pick_documents(session, limit)
        if not selection:
            print("No s'ha trobat cap document PDF a la base de dades.")
            return 1

        print(f"{len(selection)} documents seleccionats.\n")
        if args.dry_run:
            for tender, document in selection:
                print(f"  {tender.expediente_number or tender.external_id} :: {document.name}")
            return 0

        header = (
            f"{'Expedient':<22} {'Document':<28} {'Bytes':>10} {'Pag':>5} "
            f"{'Chars':>9} {'Qualitat':<12} {'ms desc':>8} {'ms parse':>9}"
        )
        print(header)
        print("-" * len(header))

        failures = 0
        ocr_candidates = 0

        for tender, document in selection:
            outcome = await process_tender_document(session, document)
            file = outcome.file
            extraction = None
            if file.content_id:
                extraction = await session.scalar(
                    sa.select(TenderDocumentExtraction).where(
                        TenderDocumentExtraction.content_id == file.content_id,
                        TenderDocumentExtraction.is_current.is_(True),
                    )
                )

            if file.download_status.value != "downloaded":
                failures += 1
                print(
                    f"{(tender.expediente_number or tender.external_id)[:21]:<22} "
                    f"{document.name[:27]:<28} ERROR {file.error_code}"
                )
                continue

            if extraction and extraction.ocr_candidate:
                ocr_candidates += 1

            print(
                f"{(tender.expediente_number or tender.external_id)[:21]:<22} "
                f"{document.name[:27]:<28} "
                f"{file.content.file_size:>10} "
                f"{(extraction.page_count if extraction else 0):>5} "
                f"{(extraction.text_char_count if extraction else 0):>9} "
                f"{(extraction.quality.value if extraction else '-'):<12} "
                f"{(file.download_ms or 0):>8} "
                f"{(extraction.parse_ms if extraction else 0):>9}"
            )
            # El contingut dels plecs no es reprodueix: nomes metriques.
            print(f"{'':22} sha256: {file.content.sha256}")

        print(f"\nFallades: {failures} · Candidats a OCR: {ocr_candidates}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

Comprova el nom real de la factoria de sessions a `apps/api/app/db/session.py` (pot ser `async_session_maker`, `AsyncSessionLocal` o similar) i ajusta l'import.

- [ ] **Step 2: Escriu la documentació**

Crea `docs/documents.md`:

````markdown
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
````

- [ ] **Step 3: Documenta la configuració a `.env.example`**

Afegeix al final de `.env.example`:

```bash
# --- Descàrrega de plecs de licitació ---
# Adreça que s'anuncia al User-Agent davant de PLACSP.
INGESTION_CONTACT_EMAIL=
DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS=30
DOCUMENT_DOWNLOAD_TOTAL_TIMEOUT_SECONDS=120
DOCUMENT_MAX_SIZE_MB=60
DOCUMENT_MAX_REDIRECTS=5
DOCUMENT_DOWNLOAD_MAX_ATTEMPTS=3
DOCUMENT_MAX_PAGES=2000
DOCUMENT_LOW_TEXT_CHARS_PER_PAGE=100
DOCUMENT_PROCESSING_STALE_SECONDS=300
# MAI a producció: permetria descarregar de la xarxa interna (SSRF).
DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS=false
```

- [ ] **Step 4: Actualitza el README**

A la taula «Què hi ha implementat» de `README.md`, substitueix la fila
`| Lectura de plecs, requisits, Go/No-Go, IA, RAG | ❌ (fase següent) |` per:

```markdown
| Descàrrega de plecs amb protecció SSRF i límits centralitzats | ✅ |
| Original immutable a S3, deduplicat per SHA-256 | ✅ |
| Versionat de documents: contingut canviat = versió nova, l'antiga es conserva | ✅ |
| Extracció de text per pàgina (pypdf, 1-based) amb qualitat determinista | ✅ |
| Preview del text extret amb navegació de pàgina | ✅ |
| Requisits, Go/No-Go, IA, RAG, OCR | ❌ (fase següent) |
```

I afegeix `docs/documents.md` a la llista d'enllaços de documentació.

- [ ] **Step 5: Verifica l'script en sec**

Run: `python scripts/process_real_documents.py --dry-run`
Expected: llista 5 documents PDF de 5 expedients diferents, sense descarregar res.

- [ ] **Step 6: Commit**

```bash
git add scripts/process_real_documents.py docs/documents.md README.md .env.example
git commit -m "docs(documents): guia de la capa documental i script de prova real

L'script esta acotat expressament amb un HARD_LIMIT: volem comprovar que la
cadena funciona amb PDF reals, no fer-nos una copia de PLACSP.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

---

## Task 12: Verificació real d'extrem a extrem

Sense codi nou. És la comprovació que el criteri d'acceptació es compleix contra PostgreSQL, MinIO i PLACSP reals, i el material per a l'informe final.

**Files:** cap (només execució i informe)

- [ ] **Step 1: Aixeca la infraestructura i migra**

Run:
```bash
docker compose up -d db minio minio-init
cd apps/api && python -m alembic upgrade head
```
Expected: `0005_tender_docs` aplicada. Comprova que `tenders` continua tenint 1.188 files.

- [ ] **Step 2: Verifica el conjunt de la suite i els linters**

Run:
```bash
cd apps/api && python -m pytest -q && python -m ruff check app tests && python -m pyflakes app scripts
cd ../web && npx tsc --noEmit && npx eslint src && npm run build
```
Expected: tot verd. Anota els números per a l'informe (tests totals i nous).

- [ ] **Step 3: Processa documents reals**

Run: `python scripts/process_real_documents.py --limit 5`
Expected: taula amb expedient, document, bytes, pàgines, caràcters, qualitat, ms de descàrrega i ms de parseig. Anota-ho tot; **no copiïs contingut dels plecs**.

Si molts documents surten `low_text` o `ocr_candidate`, **anota-ho i informa'n**. No instal·lis cap OCR.

- [ ] **Step 4: Verifica la deduplicació sobre dades reals**

Run:
```bash
cd apps/api && python -c "
import asyncio, sqlalchemy as sa
from app.db.session import async_session_maker
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile, TenderDocumentExtraction
async def main():
    async with async_session_maker() as s:
        for model in (TenderDocumentFile, TenderDocumentContent, TenderDocumentExtraction):
            n = await s.scalar(sa.select(sa.func.count()).select_from(model))
            print(f'{model.__name__}: {n}')
asyncio.run(main())
"
```
Expected: `Contents <= Files`. Si són iguals és que cap document real s'ha repetit, cosa perfectament possible amb 5 documents; anota-ho.

- [ ] **Step 5: Comprova la idempotència**

Torna a executar `python scripts/process_real_documents.py --limit 5`.
Expected: no augmenta ni el nombre de `Files` ni el de `Contents` ni el de `Extractions`, i els `download_ms` no apareixen (no s'ha baixat res).

- [ ] **Step 6: Prova de persistència**

Run:
```bash
docker compose restart db minio
cd apps/api && python -m uvicorn app.main:app --reload
```
Després, en una altra terminal, comprova que un `file_id` conegut segueix responent, que l'original es descarrega i que el seu SHA-256 coincideix amb el desat:

```bash
curl -s -b cookies.txt "http://localhost:8000/api/v1/tender-document-files/<FILE_ID>/original" \
  | sha256sum
```
Expected: el hash coincideix amb el `sha256` que dona `GET /tender-document-files/<FILE_ID>`.

- [ ] **Step 7: Verificació a la interfície**

Amb `apps/web` engegat, al navegador:

1. Obre una licitació processada. La secció Documents mostra estat, mida, pàgines i data.
2. Prem **Processa** en un document no processat i comprova que l'estat canvia.
3. Prem **Reprocessa** i comprova que no triga (reutilització).
4. Obre **Text** i navega: pàgina 1, pàgina següent, i salta a una pàgina concreta escrivint-ne el número.
5. Comprova que el text de la pàgina N es correspon amb la pàgina N de l'original obert amb **Obre ↗**. Aquesta és **la** comprovació de la iteració.
6. Comprova que un document amb error mostra el seu `error_code`.

Que l'API respongui 200 no és evidència que la interfície funcioni: cal mirar-ho.

- [ ] **Step 8: Escriu l'informe final**

Recull en un missatge: models nous · migració · biblioteca PDF i per què · estructura d'storage · proteccions SSRF · límits configurats · `parser_version` · documents reals provats amb bytes, pàgines, caràcters i temps · documents `low_text`/`ocr_candidate` · deduplicació observada · versionat · resultats de `pytest`, `pyflakes`, `tsc`, `eslint`, `next build` · bugs reals trobats · deute tècnic.

- [ ] **Step 9: Commit final i aturada**

```bash
git add -A
git commit -m "chore(documents): verificacio real de la Iteracio 4A

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015UovAhJXuw9PVZ6CAXuEnJ"
```

**ATURA'T AQUÍ.** No implementis Requirements ni cap IA. La Iteració 4B necessita autorització explícita.

---

## Self-Review

Revisió del pla contra l'spec, feta després d'escriure'l.

**Cobertura de l'spec.** Les 20 seccions tenen tasca assignada: model de dades §3 → Task 5 · deduplicació §4 → Tasks 6 i 12 · versionat i idempotència §5 → Task 6 · concurrència §6 → Tasks 5 i 6 · descàrrega i validació §7 → Tasks 3 i 4 · storage §8 → Tasks 1 i 6 · SSRF §9 → Task 2 · configuració §10 → Task 1 · enums §11 → Task 1 · API §12 → Task 7 · frontend §13 → Tasks 9 i 10 · migració §14 → Task 5 · observabilitat §15 → Tasks 3 i 6 · proves §16 → Tasks 2-7 i 12 · fitxers §17 → File Structure · elecció de biblioteca §18 → Tasks 1 i 11 · acceptació §19 → Task 12 · aturada §20 → Task 12.

**Correccions aplicades durant la revisió:**

1. La fixture `_no_backoff` de la Task 3 s'ha fet `autouse` perquè els tres tests de reintent no esperessin 6 segons cadascun.
2. El test de tall per mida amb socket real necessitava afegir el port efímer a `DOCUMENT_DOWNLOAD_ALLOWED_PORTS`; sense això la guarda el bloquejava abans de connectar i el test hauria passat per la raó equivocada.
3. `_claim` a la Task 6 assigna `is_current=False` i només el puja a `True` en tancar. Si el reclamés d'entrada, una descàrrega fallida destronaria una versió bona.
4. El cas A elimina la fila provisional en comptes de deixar-la com a versió: sense això, cada `force_download` amb contingut idèntic hauria creat una versió nova, contradient l'spec.
5. `page_number: int = Path(ge=1)` a l'endpoint de pàgina: la pàgina 0 dona 422, no 404, cosa que fa la numeració 1-based verificable des de l'API.

**Consistència de noms** (verificada entre tasques): `PARSER_VERSION` · `ExtractionResult` / `ExtractedPage` · `DownloadResult` / `DownloadError.code` · `BlockedURLError.code` · `ProcessOutcome.file` / `.reused` · `current_file_for` / `current_extraction_for` · `build_tender_document_key(sha256, extension)` · `documentState()` / `DOCUMENT_STATE_LABELS` / `DOCUMENT_STATE_TONES` / `formatBytes()`.

**Punts on l'implementador ha de verificar el codi existent abans d'escriure** (marcats a les tasques corresponents, perquè el pla no els pot endevinar): la signatura real de `Source` (Task 5), els noms de les fixtures `client` / `auth_client` i de la factoria de sessions (Tasks 7 i 11), les props reals de `CardHeader`, `PageHeader`, `Input` i els tons de `Badge` (Tasks 9 i 10), i el prefix real de les crides del frontend.
