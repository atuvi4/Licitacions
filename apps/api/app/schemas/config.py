"""Esquema de la configuracio que s'exposa al client."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClientConfig(BaseModel):
    """Limits que el frontend necessita per validar abans d'enviar res.

    No conte cap secret: nomes limits i llistes blanques que el client ja
    descobriria provant de pujar un fitxer.
    """

    max_upload_size_mb: int = Field(description="Mida maxima per fitxer, en MB.")
    allowed_mime_types: list[str] = Field(description="Tipus MIME acceptats en pujar.")
    expiry_warning_days: int = Field(
        description="Dies abans de la caducitat en que una acreditacio es marca com a 'caduca aviat'."
    )
    # No es un secret (el desplegament ja es public) i es l'unic senyal fiable
    # que te una eina externa per saber contra quin entorn parla. El fan servir
    # els smoke tests per negar-se a mutar dades en produccio: fiar-se del nom
    # de la base de dades o de la URL no serveix de res.
    environment: str = Field(description="Entorn declarat pel backend: local, staging, production...")
