"""Application configuration – constants, patterns, defaults.

Environment-driven settings are now managed by app.settings (pydantic-settings).
This module re-exports them for backward compatibility.
"""

import re
from .settings import get_settings

# ── Compiled patterns ──
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 100

# ── Settings singleton (eager) ──
_settings = get_settings()

# ── Re-exports for backward compatibility ──
APP_ENV = _settings.app_env
IS_PRODUCTION = _settings.is_production

ALLOWED_ORIGINS = _settings.parsed_origins

JWT_SECRET_KEY = _settings.jwt_secret_key
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = _settings.jwt_expire_hours

PAYMENT_PROVIDER = _settings.payment_provider
PAYMENT_WEBHOOK_SECRET = _settings.payment_webhook_secret

SMTP_HOST = _settings.smtp_host
SMTP_PORT = _settings.smtp_port
SMTP_USER = _settings.smtp_user
SMTP_PASSWORD = _settings.smtp_password
SMTP_FROM = _settings.smtp_from
SMTP_USE_TLS = _settings.smtp_use_tls
SMTP_USE_SSL = _settings.smtp_use_ssl

TERMS_VERSION = _settings.terms_version
PRIVACY_VERSION = _settings.privacy_version

UPLOADS_DIR = _settings.uploads_dir
OUTPUTS_DIR = _settings.outputs_dir
DB_PATH = _settings.db_path

AUTH_RATE_LIMIT_PER_MIN = _settings.auth_rate_limit_per_min
QUOTE_RATE_LIMIT_PER_MIN = _settings.quote_rate_limit_per_min
CAPTCHA_RATE_LIMIT_PER_MIN = _settings.captcha_rate_limit_per_min
CAPTCHA_TTL_SECONDS = _settings.captcha_ttl_seconds
CAPTCHA_LENGTH = _settings.captcha_length
CAPTCHA_MAX_ATTEMPTS = _settings.captcha_max_attempts

VERIFY_CODE_TTL_SECONDS = _settings.verify_code_ttl_seconds
VERIFY_CODE_MAX_ATTEMPTS = _settings.verify_code_max_attempts
VERIFY_SEND_RATE_LIMIT_PER_10MIN = _settings.verify_send_rate_limit_per_10min
VERIFY_SEND_COOLDOWN_SECONDS = _settings.verify_send_cooldown_seconds

IDEMPOTENCY_TTL_SECONDS = _settings.idempotency_ttl_seconds

MEMBER_DISCOUNT_PERCENT = _settings.member_discount_percent

LOGIN_FAILED_MAX_ATTEMPTS = _settings.login_failed_max_attempts
LOGIN_FAILED_WINDOW_SECONDS = _settings.login_failed_window_seconds
LOGIN_LOCK_SECONDS = _settings.login_lock_seconds

AUDIT_RETENTION_DAYS = _settings.audit_retention_days
QUOTE_CONCURRENCY = _settings.quote_concurrency

ADMIN_USERNAMES = _settings.parsed_admin_usernames

# ── Pure constants (non-env-var) ──
SUPPORTED_EXTENSIONS = {".stl", ".stp", ".step", ".obj", ".3mf"}
MAX_FILES_PER_REQUEST = 20
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024

SYSTEM_SLICER_PRESET_ID = 0

DEFAULT_COLORS = ["White", "Black", "Gray", "Red", "Blue"]
DEFAULT_MATERIALS = [
    {"name": "PLA", "density": 1.24, "price_per_kg": 200.0, "colors": DEFAULT_COLORS},
    {"name": "ABS", "density": 1.04, "price_per_kg": 250.0, "colors": DEFAULT_COLORS},
    {"name": "Resin", "density": 1.11, "price_per_kg": 800.0, "colors": DEFAULT_COLORS},
]
DEFAULT_UNIT_COST_FORMULA = "((effective_weight_g * (price_per_kg / 1000.0)) + (unit_time_h * machine_hourly_rate_cny) + post_process_fee_per_part_cny) * difficulty_multiplier + support_cost_per_part_cny"
DEFAULT_TOTAL_COST_FORMULA = "max((unit_cost_cny * quantity) + setup_fee_cny, min_job_fee_cny)"
DEFAULT_PRICING_CONFIG = {
    "machine_hourly_rate_cny": 15.0,
    "setup_fee_cny": 0.0,
    "min_job_fee_cny": 0.0,
    "material_waste_percent": 5.0,
    "support_percent_of_model": 0.0,
    "post_process_fee_per_part_cny": 0.0,
    "difficulty_coefficient": 0.25,
    "difficulty_ratio_low": 0.8,
    "difficulty_ratio_high": 4.0,
    "use_bambu": 0,
    "use_prusaslicer": 0,
    "prusa_time_correction": 0.44,
    "bambu_support_mode": "diff",
    "support_price_per_g": 0.0,
    "time_overhead_min": 5.0,
    "time_vol_min_per_cm3": 0.8,
    "time_area_min_per_cm2": 0.0,
    "time_ref_layer_height_mm": 0.2,
    "time_layer_height_exponent": 1.0,
    "time_ref_infill_percent": 20.0,
    "time_infill_coefficient": 1.0,
    "unit_cost_formula": DEFAULT_UNIT_COST_FORMULA,
    "total_cost_formula": DEFAULT_TOTAL_COST_FORMULA,
}

APP_DEFAULTS_KEY = "global_defaults_v1"
