"""Configuracio que el client necessita coneixer.

Existeix per una rao concreta: el limit de mida i la llista de tipus MIME
acceptats els decideix el backend (variables d'entorn) i el frontend els ha de
poder mostrar i pre-validar. Duplicar-los al codi del frontend garanteix que
algun dia divergiran; aqui hi ha una unica font de veritat.

El backend segueix sent l'autoritat: aquests valors son informatius per a la UX,
la validacio real es fa igualment en pujar el fitxer.
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.config import settings
from app.schemas.config import ClientConfig

router = APIRouter(prefix="/config", tags=["infra"])


@router.get("", response_model=ClientConfig, summary="Limits i opcions per al client")
async def get_client_config(_: CurrentUser) -> ClientConfig:
    return ClientConfig(
        max_upload_size_mb=settings.MAX_UPLOAD_SIZE_MB,
        allowed_mime_types=sorted(settings.allowed_mime_types),
        expiry_warning_days=settings.EXPIRY_WARNING_DAYS,
        environment=settings.ENVIRONMENT,
    )
