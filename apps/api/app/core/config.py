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

    # --- Regles de negoci ---
    # Dies abans de la caducitat a partir dels quals una acreditacio es "expiring_soon".
    EXPIRY_WARNING_DAYS: int = 60

    @model_validator(mode="after")
    def _secret_key_is_strong_enough(self) -> "Settings":
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
