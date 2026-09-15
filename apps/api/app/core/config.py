"""Configuracio de l'aplicacio.

Tots els secrets viuen aqui i nomes al backend. El frontend mai rep credencials:
parla amb l'API a traves d'un proxy same-origin.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General ---
    ENVIRONMENT: str = "local"
    LOG_LEVEL: str = "INFO"
    PROJECT_NAME: str = "Plataforma de Licitacions"
    API_V1_PREFIX: str = "/api/v1"

    # --- Base de dades ---
    POSTGRES_USER: str = "licitacions"
    POSTGRES_PASSWORD: str = "licitacions"
    POSTGRES_DB: str = "licitacions"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    # Si s'informa, te prioritat sobre els camps POSTGRES_* (util per a tests i CI).
    DATABASE_URL: str | None = None

    # --- Seguretat ---
    SECRET_KEY: str = "insecure-development-key-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    SESSION_COOKIE_NAME: str = "licitacions_session"
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- Storage ---
    STORAGE_BACKEND: str = "local"  # s3 | local
    STORAGE_BUCKET: str = "licitacions-documents"
    STORAGE_ENDPOINT_URL: str | None = None
    STORAGE_REGION: str = "us-east-1"
    STORAGE_ACCESS_KEY: str | None = None
    STORAGE_SECRET_KEY: str | None = None
    STORAGE_FORCE_PATH_STYLE: bool = True
    STORAGE_LOCAL_PATH: str = "./var/storage"

    # --- Uploads ---
    MAX_UPLOAD_SIZE_MB: int = 25
    ALLOWED_UPLOAD_MIME_TYPES: str = Field(
        default=(
            "application/pdf,"
            "image/png,image/jpeg,image/tiff,image/webp,"
            "application/msword,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.ms-excel,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "application/vnd.ms-powerpoint,"
            "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
            "text/plain,text/csv,application/zip"
        )
    )

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
            # Familia OLE2 (.doc/.xls/.ppt anteriors a 2007) quan els magic bytes
            # no permeten afinar-ne el tipus. Es conserva l'original; no se n'extreu text.
            "application/x-ole-storage,"
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

    # --- Capa d'IA (extraccio de requisits) ---
    # Proveidor d'inferencia:
    #   "anthropic" -> analisi real.
    #   "fixture"   -> SIMULACIO: un plec inventat i estable per poder recorrer
    #                  el flux sencer en desenvolupament sense xarxa ni API.
    #   "fake"      -> doble de proves: respon el que cada test li encua i, per
    #                  defecte, no res. Si apareix davant d'un usuari vol dir
    #                  que no hi ha cap motor configurat, i aixi es diu.
    # Cap dels dos ultims no arrenca en produccio: veure el validador de sota.
    LLM_PROVIDER: str = "fake"
    # Buida = la funcio d'analisi queda no disponible, pero l'aplicacio arrenca.
    ANTHROPIC_API_KEY: str = ""
    # El model NO es una decisio de domini: es configuracio.
    LLM_MODEL: str = "claude-sonnet-5"
    LLM_TIMEOUT_SECONDS: float = 120.0
    LLM_MAX_RETRIES: int = 2
    LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS: int = 8000
    # Preu dels models, en dolars per milio de tokens, per poder posar un cost a
    # una execucio del benchmark. Buit per defecte: un preu al codi envelleix
    # sense que ningu no ho noti, i un cost calculat amb un preu fals es pitjor
    # que no tenir-ne cap. Si el model no hi es, `estimated_cost` queda buit i
    # l'informe diu per que. Exemple:
    #   LLM_MODEL_PRICES={"<model>": {"input": <USD per 1M>, "output": <USD per 1M>}}
    LLM_MODEL_PRICES: dict[str, dict[str, float]] = Field(default_factory=dict)

    # --- Analisi de requisits ---
    # Chunking per pagines: mai es parteix una pagina entre chunks.
    REQUIREMENTS_CHUNK_PAGES: int = 6
    # Solapament perque un requisit que travessa un salt de pagina no es perdi.
    REQUIREMENTS_CHUNK_OVERLAP_PAGES: int = 1
    # Sostre de pagines per execucio: evita cremar tokens amb un expedient enorme.
    REQUIREMENTS_MAX_PAGES_PER_RUN: int = 400
    # El model ha de citar 1-3 frases, no pagines senceres.
    REQUIREMENTS_MAX_QUOTE_CHARS: int = 600
    # Una analisi en curs mes antiga que aixo es considera morta i es pot reclamar.
    REQUIREMENTS_ANALYSIS_STALE_SECONDS: int = 900

    # --- Feina asincrona (worker) ---
    # Durada del lease d'un job en curs. Si no es renova, el job es considera
    # abandonat: els de documents es tornen a encuar i els d'analisi es queden
    # orfes per reconciliar-los (mai no es reexecuten sols).
    JOB_LEASE_SECONDS: int = 120
    # Cada quant renova el lease el worker mentre treballa.
    JOB_HEARTBEAT_SECONDS: int = 15
    # Cada quant mira la cua un worker desocupat.
    JOB_POLL_SECONDS: float = 2.0
    # Intents d'un job de documents (idempotent i sense cost de proveidor).
    JOB_DOCUMENT_MAX_ATTEMPTS: int = 3
    # Temps que es deixa a un worker d'analisi que ha perdut el lease abans de
    # donar-lo per mort. Ha de cobrir la crida al proveidor mes llarga possible
    # (timeout x intents) i el tancament del run.
    JOB_ORPHAN_GRACE_SECONDS: int = 900

    # --- Control de cost de l'analisi (pilot) ---
    # Cap anàlisi no es llança sense que l'usuari n'hagi vist el volum i l'hagi
    # confirmat. Aquests valors son de configuracio, no de negoci: el pilot els
    # ha de poder ajustar sense tocar codi.
    # Chunks maxims d'una analisi. Per sobre, el preview surt `allowed: false`.
    ANALYSIS_MAX_CHUNKS_PER_RUN: int = 40
    # Peticions d'analisi pendents simultanies per empresa.
    ANALYSIS_MAX_ACTIVE_PER_COMPANY: int = 2
    # Temps durant el qual es pot confirmar un preview.
    ANALYSIS_QUOTE_TTL_SECONDS: int = 900
    # Rols que poden confirmar una analisi (i per tant fer despesa). `technical`
    # en queda fora per defecte: prepara la candidatura, no compromet despesa.
    ANALYSIS_CONFIRM_ROLES: list[str] = Field(
        default_factory=lambda: ["owner", "admin", "bid_manager"]
    )
    # Rols que poden demanar el processat dels plecs.
    DOCUMENT_PROCESS_ROLES: list[str] = Field(
        default_factory=lambda: ["owner", "admin", "bid_manager"]
    )

    # --- Benchmark d'extraccio ---
    # La base de dades on s'executen els benchmarks, amb el seu propi corpus.
    # Buida per defecte: el benchmark no ha de poder arrencar per descuit contra
    # la base de desenvolupament. `app/evaluation/benchmark.py` refusa per nom
    # `licitacions` i `licitacions_test`, passin per aqui o per la linia d'ordres.
    BENCHMARK_DATABASE_URL: str = ""

    # --- Regles de negoci ---
    # Dies abans de la caducitat a partir dels quals una acreditacio es "expiring_soon".
    EXPIRY_WARNING_DAYS: int = 60

    @model_validator(mode="after")
    def _secret_key_is_strong_enough(self) -> Settings:
        """A produccio, una clau curta o la d'exemple no son acceptables.

        HS256 amb una clau de pocs bytes es trivialment atacable per forca
        bruta. A local es tolera per comoditat, pero no mes enlla.
        """
        if self.is_production:
            if len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "SECRET_KEY ha de tenir com a minim 32 caracters en produccio. "
                    'Genera una clau amb: python -c "import secrets; '
                    'print(secrets.token_urlsafe(64))"'
                )
            if "change-me" in self.SECRET_KEY or "insecure" in self.SECRET_KEY:
                raise ValueError("SECRET_KEY encara es el valor d'exemple.")
        return self

    @model_validator(mode="after")
    def _private_downloads_are_never_allowed_in_production(self) -> Settings:
        """Un downloader que accepta IP privades a produccio es una SSRF oberta.

        Es llanca en comptes de corregir el valor en silenci: una configuracio
        de seguretat mal posada ha de fer soroll, no desapareixer.
        """
        if self.is_production and self.DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS:
            raise ValueError(
                "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS nomes es pot activar fora de produccio."
            )
        return self

    @model_validator(mode="after")
    def _production_needs_a_real_inference_provider(self) -> Settings:
        """A produccio no s'hi pot analitzar amb un proveidor que no llegeix res.

        `fake` i `fixture` responen sense obrir el plec. En desenvolupament aixo
        es util —i la interficie ho diu amb totes les lletres—, pero a produccio
        significaria ensenyar requisits inventats amb l'aparença d'una analisi.
        Val mes no arrencar que servir aixo.

        Es fa amb el mateix mecanisme que la resta de politiques d'entorn
        (`is_production` + validador que llanca), no amb un de nou.
        """
        if self.is_production and self.LLM_PROVIDER.strip().lower() in {"fake", "fixture"}:
            raise ValueError(
                f"LLM_PROVIDER={self.LLM_PROVIDER!r} no llegeix cap plec i no es pot "
                "fer servir en produccio. Configura un proveidor real."
            )
        return self

    @field_validator("ANALYSIS_CONFIRM_ROLES", "DOCUMENT_PROCESS_ROLES")
    @classmethod
    def _roles_must_exist(cls, value: list[str]) -> list[str]:
        from app.models.enums import MembershipRole

        known = {role.value for role in MembershipRole}
        roles = [str(role).strip().lower() for role in value]
        unknown = sorted(set(roles) - known)
        if unknown:
            raise ValueError(f"Rols desconeguts: {', '.join(unknown)}.")
        return roles

    @field_validator("COOKIE_SAMESITE")
    @classmethod
    def _validate_samesite(cls, value: str) -> str:
        allowed = {"lax", "strict", "none"}
        if value.lower() not in allowed:
            raise ValueError(f"COOKIE_SAMESITE ha de ser un de {sorted(allowed)}")
        return value.lower()

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_mime_types(self) -> set[str]:
        return {m.strip() for m in self.ALLOWED_UPLOAD_MIME_TYPES.split(",") if m.strip()}

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

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

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
