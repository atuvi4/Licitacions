"""Configuracio minima de logging.

Regla del projecte: als logs no hi van contrasenyes, tokens, ni contingut de
documents. Nomes identificadors.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True
