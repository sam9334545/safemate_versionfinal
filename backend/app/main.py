"""FastAPI Application entry point with lifespan, middleware, and exception handlers."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from pathlib import Path
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, chatbot, deepfake, detect, emergency, evidence, health, model_info, routes, safewalk, watermark
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.schemas.errors import AppException
from app.services.model_service import model_service
from app.services.video_deepfake_service import video_deepfake_service
from app import models
from app.database import engine, safe_migrate_tables
from app.utils.request_id import get_request_id, set_request_id

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown lifecycle events."""
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} in '{settings.ENVIRONMENT}' mode.")
    logger.info(f"Configured CORS origins: {settings.CORS_ORIGINS}")
    logger.info(f"Configured max upload: {settings.MAX_UPLOAD_MB} MB")

    # Initialize database tables & apply safe schema migrations
    logger.info("Initializing database tables on startup...")
    try:
        models.Base.metadata.create_all(bind=engine)
        safe_migrate_tables(engine, models.Base)
        logger.info("Database tables initialized and schema verified successfully.")
    except Exception as exc:
        logger.warning(f"Database table initialization warning: {type(exc).__name__}: {exc}")

    # Conditional model preloading to avoid out-of-memory crashes on cloud platforms
    if settings.PRELOAD_MODELS_ON_STARTUP:
        # Load pretrained still-image model
        logger.info(f"Attempting model startup load for: {settings.MODEL_ID}")
        try:
            model_service.load()
        except Exception as exc:
            logger.warning(f"Initial model loading failed: {type(exc).__name__}. Will report unready until loaded.")

        # Load pretrained video deepfake model
        logger.info(f"Attempting video deepfake startup load for: {settings.VIDEO_DEEPFAKE_MODEL_ID}")
        try:
            video_deepfake_service.load()
        except Exception as exc:
            logger.warning(f"Initial video deepfake model loading failed: {type(exc).__name__}. Will load lazily or report unready.")
    else:
        logger.info(
            "Cloud low-memory mode active (PRELOAD_MODELS_ON_STARTUP=False). "
            "Heavy neural models will load on demand or via Hugging Face API to protect container RAM."
        )

    yield

    logger.info(f"Shutting down {settings.APP_NAME}.")
    if model_service.model is not None:
        del model_service.model
        del model_service.processor
        model_service.model = None
        model_service.processor = None
        model_service.is_loaded = False

    if video_deepfake_service.model is not None:
        del video_deepfake_service.model
        del video_deepfake_service.detector
        video_deepfake_service.model = None
        video_deepfake_service.detector = None
        video_deepfake_service.is_loaded = False


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI instance."""
    # Ensure database tables exist
    try:
        models.Base.metadata.create_all(bind=engine)
        safe_migrate_tables(engine, models.Base)
        logger.info("Database tables verified/created via create_app.")
    except Exception as exc:
        logger.warning(f"Database table initialization warning: {type(exc).__name__}: {exc}")

    app = FastAPI(
        title="AI Image Screening API",
        description=(
            "REST API for screening AI-generated images in a women's safety application. "
            "Exposes pre-trained Hugging Face classification with privacy-first, in-memory processing."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 1. Custom Request ID Middleware
    class RequestIDMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            rid = set_request_id(request.headers.get("X-Request-ID"))
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response

    app.add_middleware(RequestIDMiddleware)

    # 2. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "Content-Disposition",
            "X-Watermark-Recipient",
            "X-Watermark-ID",
            "X-Watermark-Fingerprint",
            "X-Recipient-Count",
        ],
    )

    # 3. Exception Handlers
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        rid = exc.request_id or get_request_id()
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = get_request_id()
        # Formulate readable message without exposing internal details
        error_messages = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            msg = err.get("msg", "Validation error")
            error_messages.append(f"{loc}: {msg}" if loc else msg)
        combined_msg = "; ".join(error_messages) or "Request validation failed."

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": combined_msg,
                },
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = get_request_id()
        logger.error(f"Unhandled exception on {request.url.path} [ReqID: {rid}]: {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal server error occurred. Please contact support with the request ID.",
                },
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )

    # 4. Include Routers
    app.include_router(health.router, prefix=settings.API_V1_PREFIX)
    app.include_router(model_info.router, prefix=settings.API_V1_PREFIX)
    app.include_router(detect.router, prefix=settings.API_V1_PREFIX)
    app.include_router(deepfake.router, prefix=settings.API_V1_PREFIX)
    app.include_router(emergency.router)
    app.include_router(safewalk.router)
    app.include_router(evidence.router)
    app.include_router(chatbot.router)
    app.include_router(auth.router)
    app.include_router(watermark.router)
    app.include_router(routes.router)

    # Mount static assets for chatbot UI
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount frontend web application static files if directory exists
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/app", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        assets_dir = frontend_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/chatbot", tags=["Chatbot UI"])
    def chatbot_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/api/v1/chatbot/ui")

    @app.get("/", tags=["Root"])
    def root(request: Request):
        accept = request.headers.get("accept", "")
        if "text/html" in accept and "application/json" not in accept:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/app/")
        return {
            "name": settings.APP_NAME,
            "version": "1.0.0",
            "docs": "/docs",
            "web_app": "/app",
            "health": f"{settings.API_V1_PREFIX}/health",
            "ready": f"{settings.API_V1_PREFIX}/health/ready",
            "model_info": f"{settings.API_V1_PREFIX}/model-info",
            "image_screening": f"{settings.API_V1_PREFIX}/detect",
            "video_deepfake_screening": f"{settings.API_V1_PREFIX}/deepfake/detect",
            "video_deepfake_ready": f"{settings.API_V1_PREFIX}/deepfake/ready",
            "video_deepfake_model_info": f"{settings.API_V1_PREFIX}/deepfake/model-info",
            "emergency_sos": f"{settings.API_V1_PREFIX}/emergency/sos",
            "emergency_contacts": f"{settings.API_V1_PREFIX}/emergency/contacts",
            "emergency_alerts": f"{settings.API_V1_PREFIX}/emergency/alerts",
            "emergency_call": f"{settings.API_V1_PREFIX}/emergency/call",
            "emergency_resolve": f"{settings.API_V1_PREFIX}/emergency/sos/{{alert_id}}/resolve",
            "safewalk_create_trip": f"{settings.API_V1_PREFIX}/safewalk/create-trip",
            "safewalk_trip": f"{settings.API_V1_PREFIX}/safewalk/trip/{{token}}",
            "safewalk_update_location": f"{settings.API_V1_PREFIX}/safewalk/update-location",
            "safewalk_complete_trip": f"{settings.API_V1_PREFIX}/safewalk/trip/{{token}}/complete",
            "safewalk_cancel_trip": f"{settings.API_V1_PREFIX}/safewalk/trip/{{token}}/cancel",
            "safewalk_escalate_trip": f"{settings.API_V1_PREFIX}/safewalk/trip/{{token}}/escalate",
            "safewalk_my_trips": f"{settings.API_V1_PREFIX}/safewalk/my-trips",
            "evidence_upload": f"{settings.API_V1_PREFIX}/evidence/upload",
            "evidence_download": f"{settings.API_V1_PREFIX}/evidence/download/{{evidence_id}}",
            "evidence_record": f"{settings.API_V1_PREFIX}/evidence/record/{{evidence_id}}",
            "chatbot_ui": "/chatbot",
            "chatbot_health": f"{settings.API_V1_PREFIX}/chatbot/health",
            "chatbot_resources": f"{settings.API_V1_PREFIX}/chatbot/resources",
            "chatbot_countries": f"{settings.API_V1_PREFIX}/chatbot/countries",
            "chatbot_emergency_override": f"{settings.API_V1_PREFIX}/chatbot/emergency-override",
            "chatbot_flows": f"{settings.API_V1_PREFIX}/chatbot/flows",
            "chatbot_start": f"{settings.API_V1_PREFIX}/chatbot/start",
            "chatbot_message": f"{settings.API_V1_PREFIX}/chatbot/message",
            "chatbot_draft_complaint": f"{settings.API_V1_PREFIX}/chatbot/draft-complaint",
            "chatbot_history": f"{settings.API_V1_PREFIX}/chatbot/history/{{session_id}}",
            "chatbot_export": f"{settings.API_V1_PREFIX}/chatbot/export/{{session_id}}",
            "chatbot_tell_once_start": f"{settings.API_V1_PREFIX}/chatbot/tell-once/start",
            "chatbot_tell_once_turn": f"{settings.API_V1_PREFIX}/chatbot/tell-once/turn",
            "chatbot_tell_once_dossier": f"{settings.API_V1_PREFIX}/chatbot/tell-once/{{intake_id}}/dossier",
            "auth_register": f"{settings.API_V1_PREFIX}/auth/register",
            "auth_login": f"{settings.API_V1_PREFIX}/auth/login",
            "auth_login_json": f"{settings.API_V1_PREFIX}/auth/login-json",
            "auth_guest_session": f"{settings.API_V1_PREFIX}/auth/guest-session",
            "auth_change_password": f"{settings.API_V1_PREFIX}/auth/change-password",
            "auth_refresh": f"{settings.API_V1_PREFIX}/auth/refresh",
            "leak_tracer_embed": f"{settings.API_V1_PREFIX}/watermark/embed",
            "leak_tracer_batch": f"{settings.API_V1_PREFIX}/watermark/batch-embed",
            "leak_tracer_extract": f"{settings.API_V1_PREFIX}/watermark/extract",
        }

    return app


app = create_app()
