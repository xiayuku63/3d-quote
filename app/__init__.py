"""Application factory for pricer3d."""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .config import ALLOWED_ORIGINS, IS_PRODUCTION, APP_ENV
from .middleware import security_middleware
from .logging_config import setup_logging
from .errors import register_exception_handlers

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # Startup
    from .database import init_db
    from .db import init_orm
    from .utils import _uploads_base_dir, _outputs_base_dir

    pricer_logger = setup_logging()
    pricer_logger.info("event=startup env=%s", APP_ENV)
    init_db()
    init_orm()
    _uploads_base_dir()
    _outputs_base_dir()
    # Restore rate limit state from DB
    try:
        from .middleware import rate_limiter
        rate_limiter.restore_state()
    except Exception:
        pass
    logger.info("pricer3d startup complete, env=%s", APP_ENV)

    yield  # App runs here

    # Shutdown
    pricer_logger.info("event=shutdown")
    logger.info("pricer3d shutting down")


def create_app() -> FastAPI:
    app = FastAPI(title="pricer3d — 3D Printing Quoting System", lifespan=lifespan)

    # exception handlers (unified {code, message, data} format)
    register_exception_handlers(app)

    # static files
    os.makedirs("static", exist_ok=True)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    # security middleware
    app.middleware("http")(security_middleware)

    # ─── register routes ───
    from .routes_auth import (
        get_captcha, get_captcha_image, send_verify_code, confirm_verify_code,
        check_register_exists, register, login, auth_me,
        password_reset_request, password_reset_confirm,
    )
    from .routes_user import get_user_settings, update_user_settings, change_password
    from .routes_slicer import (
        api_list_slicer_presets, api_generate_slicer_preset, api_upsert_slicer_preset,
        api_download_slicer_preset, api_delete_slicer_preset, api_list_printers,
    )
    from .routes_admin import (
        admin_get_defaults, admin_set_defaults_from_me, admin_list_users,
        admin_update_user_membership, admin_list_audit, admin_metrics, admin_cleanup,
        admin_backup_create, admin_backup_list, admin_backup_cleanup,
    )
    from .routes_billing import (
        billing_plans, billing_checkout, billing_orders, billing_mock_complete, billing_webhook,
    )
    from .routes_quote import get_quote, validate_formula, quote_history
    from .routes_pages import (
        index, register_page, legal_terms, legal_privacy, admin_users_page,
        pay_mock, healthz, readyz, version,
    )
    from .schemas.auth import TokenResponse, CaptchaResponse
    from .schemas.quote import QuoteResponse, FormulaValidateRequest, QuoteHistoryItem
    from .schemas.common import PaginatedData
    from .schemas.user import MembershipPlan, BillingOrder

    # auth
    app.get("/api/auth/captcha")(get_captcha)
    app.get("/api/auth/captcha/image/{captcha_id}")(get_captcha_image)
    app.post("/api/auth/verify/send")(send_verify_code)
    app.post("/api/auth/verify/confirm")(confirm_verify_code)
    app.post("/api/auth/register/check")(check_register_exists)
    app.post("/api/auth/register")(register)
    app.post("/api/auth/login")(login)
    app.get("/api/auth/me", response_model=dict)(auth_me)
    app.post("/api/auth/password/reset/request")(password_reset_request)
    app.post("/api/auth/password/reset/confirm")(password_reset_confirm)

    # user
    app.get("/api/user/settings")(get_user_settings)
    app.put("/api/user/settings")(update_user_settings)
    app.post("/api/users/change-password")(change_password)

    # slicer
    app.get("/api/slicer/presets")(api_list_slicer_presets)
    app.post("/api/slicer/presets/generate")(api_generate_slicer_preset)
    app.post("/api/slicer/presets")(api_upsert_slicer_preset)
    app.get("/api/slicer/presets/{preset_id}/download")(api_download_slicer_preset)
    app.delete("/api/slicer/presets/{preset_id}")(api_delete_slicer_preset)
    app.get("/api/slicer/printers")(api_list_printers)

    # admin
    app.get("/api/admin/defaults")(admin_get_defaults)
    app.post("/api/admin/defaults/from-me")(admin_set_defaults_from_me)
    app.get("/api/admin/users")(admin_list_users)
    app.post("/api/admin/users/{user_id}/membership")(admin_update_user_membership)
    app.get("/api/admin/audit")(admin_list_audit)
    app.get("/api/admin/metrics")(admin_metrics)
    app.post("/api/admin/maintenance/cleanup")(admin_cleanup)
    app.post("/api/admin/maintenance/backup")(admin_backup_create)
    app.get("/api/admin/maintenance/backup")(admin_backup_list)
    app.post("/api/admin/maintenance/backup/cleanup")(admin_backup_cleanup)

    # billing
    app.get("/api/billing/plans", response_model=PaginatedData[MembershipPlan])(billing_plans)
    app.post("/api/billing/checkout", response_model=dict)(billing_checkout)
    app.get("/api/billing/orders", response_model=PaginatedData[BillingOrder])(billing_orders)
    app.post("/api/billing/mock/complete")(billing_mock_complete)
    app.post("/api/billing/webhook")(billing_webhook)

    # quote
    app.post("/api/quote", response_model=QuoteResponse)(get_quote)
    app.get("/api/quote/history", response_model=PaginatedData[QuoteHistoryItem])(quote_history)
    app.post("/api/formula/validate", response_model=dict)(validate_formula)

    # pages
    app.get("/", response_class=HTMLResponse)(index)
    app.get("/register", response_class=HTMLResponse)(register_page)
    app.get("/legal/terms", response_class=HTMLResponse)(legal_terms)
    app.get("/legal/privacy", response_class=HTMLResponse)(legal_privacy)
    app.get("/admin/users", response_class=HTMLResponse)(admin_users_page)
    app.get("/pay/mock", response_class=HTMLResponse)(pay_mock)

    # health
    app.get("/healthz")(healthz)
    app.get("/readyz")(readyz)
    app.get("/api/version")(version)

    return app
