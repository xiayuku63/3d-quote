"""Application settings — loaded from env vars via pydantic-settings."""

import re
from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Environment ──
    app_env: str = "development"
    allowed_origins: str = "https://www.pricer3d.top,https://pricer3d.top,http://localhost:3000,http://127.0.0.1:3000"

    # ── Secrets (required in production) ──
    jwt_secret_key: str = ""
    payment_webhook_secret: str = ""

    # ── Database ──
    db_path: str = "app.db"

    # ── Uploads / Outputs ──
    uploads_dir: str = "uploads"
    outputs_dir: str = "outputs"
    user_data_dir: str = "user"

    # ── SMTP ──
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    # ── Payment ──
    payment_provider: str = "mock"

    # ── Rate limits ──
    auth_rate_limit_per_min: int = 12
    quote_rate_limit_per_min: int = 30
    captcha_rate_limit_per_min: int = 60
    verify_send_rate_limit_per_10min: int = 6
    verify_send_cooldown_seconds: int = 60

    # ── Captcha ──
    captcha_ttl_seconds: int = 180
    captcha_length: int = 4
    captcha_max_attempts: int = 5

    # ── Verification ──
    verify_code_ttl_seconds: int = 600
    verify_code_max_attempts: int = 6

    # ── Auth ──
    login_failed_max_attempts: int = 6
    login_failed_window_seconds: int = 900
    login_lock_seconds: int = 900
    jwt_expire_hours: int = 24

    # ── Idempotency ──
    idempotency_ttl_seconds: int = 86400

    # ── Membership ──
    member_discount_percent: float = 0.0

    # ── Audit ──
    audit_retention_days: int = 90

    # ── Quote ──
    quote_concurrency: int = 4

    # ── Admin ──
    admin_usernames: str = "admin"

    # ── Legal ──
    terms_version: str = "v1"
    privacy_version: str = "v1"

    # ── Derived / computed ──
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def parsed_origins(self) -> list[str]:
        return [
            o.strip()
            for o in self.allowed_origins.split(",")
            if o.strip()
        ]

    @property
    def parsed_admin_usernames(self) -> set[str]:
        return {
            x.strip().lower()
            for x in self.admin_usernames.split(",")
            if x.strip()
        }

    def _validate_secrets(self) -> None:
        """In production, require secrets to be set. Call at startup."""
        errors: list[str] = []
        if self.is_production:
            if not self.jwt_secret_key or self.jwt_secret_key == "dev-only-insecure-secret-change-me":
                errors.append("JWT_SECRET_KEY 不能为空或使用开发默认值")
            if not self.payment_webhook_secret or self.payment_webhook_secret == "dev-only-insecure-secret-change-me":
                errors.append("PAYMENT_WEBHOOK_SECRET 不能为空或使用开发默认值")
        if errors:
            raise RuntimeError("配置校验失败：\n- " + "\n- ".join(errors))


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        # Provide dev defaults for secrets when unset
        if not _settings.jwt_secret_key:
            _settings.jwt_secret_key = "dev-only-insecure-secret-change-me"
        if not _settings.payment_webhook_secret:
            _settings.payment_webhook_secret = "dev-only-insecure-secret-change-me"
        _settings._validate_secrets()
    return _settings
