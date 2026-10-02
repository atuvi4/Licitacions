"""Errors de domini i format d'error estructurat de l'API.

Totes les respostes d'error tenen la mateixa forma:

    {"error": {"code": "not_found", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Error de domini controlat."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AnalysisPrerequisitesMissingError(ConflictError):
    """No hi ha res que analitzar. `details["missing"]` diu que, amb codis tancats."""

    code = "analysis_prerequisites_missing"


class AnalysisRunConflictError(ConflictError):
    """Una altra peticio ha canviat l'analisi alhora: es pot tornar a provar."""

    code = "analysis_run_conflict"


class DuplicateDocumentError(ConflictError):
    """Ja existeix un document amb el mateix SHA-256 dins de l'empresa."""

    code = "duplicate_document"


class ProcessingInProgressError(ConflictError):
    """Ja hi ha una descarrega en curs per a aquest document."""

    code = "document_processing_in_progress"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class NoContentToExtractError(ValidationError):
    """S'ha demanat re-extreure text d'un document que mai s'ha descarregat be."""

    code = "no_content_to_extract"


class NothingAnalyzableError(ValidationError):
    """Cap document de la licitacio te text extret per analitzar.

    Un plec en OLE2 o un ZIP no son un error del sistema: simplement no hi ha
    text. No s'inventa el contingut.
    """

    code = "nothing_analyzable"


class AnalysisInProgressError(ConflictError):
    """Ja hi ha una analisi de requisits en curs per a aquesta licitacio."""

    code = "analysis_in_progress"


class AnalysisInputChangedError(ConflictError):
    """Els plecs han canviat des que es va demanar l'analisi.

    No s'analitza en silenci una versio diferent de la confirmada: cal una
    peticio nova.
    """

    code = "input_changed"


class AnalysisInterruptedError(AppError):
    """L'execucio s'ha aturat abans d'un chunk perque ha perdut el lease.

    No es un error HTTP: el run queda en curs per reconciliar-lo.
    """

    code = "analysis_interrupted"


class AnalysisRunReclaimedError(ConflictError):
    """El run ja no esta en curs quan s'intentava tancar: algu l'ha reclamat."""

    code = "analysis_run_reclaimed"


class OperationBusyError(ConflictError):
    """No es pot acceptar la peticio ara mateix. No depen de cap altre tenant."""

    code = "operation_busy"


class JobRequestNotPendingError(ConflictError):
    code = "job_request_not_pending"


class AnalysisConfirmationRequiredError(ConflictError):
    """L'analisi s'ha de previsualitzar i confirmar abans de demanar-la."""

    code = "analysis_confirmation_required"


class AnalysisTooLargeError(ValidationError):
    """L'analisi supera el limit de chunks configurat per al pilot."""

    code = "analysis_too_large"


class ResumeNotAllowedError(ConflictError):
    """No es pot reprendre aquest run parcial: no es exactament la mateixa analisi.

    `details["reason"]` diu que no coincideix, sense cap text del plec.
    """

    code = "resume_not_allowed"


class CompanyActiveLimitError(AppError):
    """L'empresa ja te el maxim d'analisis pendents."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "company_active_limit"


class QuoteNotOpenError(ConflictError):
    code = "quote_not_open"


class EndpointMovedError(AppError):
    """L'operacio llarga ja no s'executa en una peticio HTTP."""

    status_code = status.HTTP_410_GONE
    code = "endpoint_moved"


class ProviderUnavailableError(AppError):
    """El proveidor d'IA no esta configurat o no ha respost."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "llm_provider_unavailable"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class PasswordChangeRequiredError(PermissionDeniedError):
    """Sessio amb contrasenya temporal: nomes es pot canviar-la (o sortir)."""

    code = "password_change_required"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """El cos d'un error, amb els `details` ja en tipus que el JSON entengui.

    Els detalls son dades del domini: identificadors, dates, imports, enums i
    llistes d'aixo. Son informacio correcta i sovint es el mes util de l'error
    —quin fitxer no quadra, quins blocadors queden— pero si arriben aqui tal
    qual, el serialitzador els rebutja i un 409 llegible es converteix en un 500
    sense codi ni detall, precisament quan mes falta fa saber que ha passat.

    Es fa servir `jsonable_encoder`, que ja resol UUID, dates, enums, models i
    estructures niades, i no un convertidor propi en paral·lel. L'unic afegit es
    `Decimal` com a CADENA: aixi ho serialitza la resta de l'API i convertir-lo
    a coma flotant, com fa l'encoder per defecte, perdria decimals d'un import.
    """
    return {
        "error": {
            "code": code,
            "message": message,
            "details": jsonable_encoder(details or {}, custom_encoder={Decimal: str}),
        }
    }


_STATUS_CODE_NAMES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODE_NAMES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())[1:]),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "validation_error",
                "Les dades enviades no son valides.",
                {"fields": fields},
            ),
        )
