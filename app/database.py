"""Database connection and initialization."""

import sqlite3
import json
from datetime import datetime, timezone

from .config import (
    DB_PATH,
    DEFAULT_MATERIALS,
    DEFAULT_COLORS,
    DEFAULT_PRICING_CONFIG,
    APP_DEFAULTS_KEY,
)
from .utils import normalize_materials


def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slicer_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                ext TEXT NOT NULL,
                content_b64 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_slicer_presets_user_id ON slicer_presets (user_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_defaults (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by INTEGER,
                updated_by_username TEXT
            )
            """
        )
        _safe_add_column(conn, "users", "materials", "TEXT")
        _safe_add_column(conn, "users", "colors", "TEXT")
        _safe_add_column(conn, "users", "pricing_config", "TEXT")
        _safe_add_column(conn, "users", "email", "TEXT")
        _safe_add_column(conn, "users", "phone", "TEXT")
        _safe_add_column(conn, "users", "email_verified", "INTEGER")
        _safe_add_column(conn, "users", "phone_verified", "INTEGER")
        _safe_add_column(conn, "users", "membership_level", "TEXT")
        _safe_add_column(conn, "users", "membership_expires_at", "TEXT")
        _safe_add_column(conn, "users", "terms_accepted_at", "TEXT")
        _safe_add_column(conn, "users", "privacy_accepted_at", "TEXT")
        _safe_add_column(conn, "users", "terms_version", "TEXT")
        _safe_add_column(conn, "users", "privacy_version", "TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                target TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_codes_target ON verification_codes (channel, target)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users (email) WHERE email IS NOT NULL AND email != ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users (phone) WHERE phone IS NOT NULL AND phone != ''")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                ip TEXT,
                method TEXT,
                path TEXT,
                request_id TEXT,
                idempotency_key TEXT,
                detail_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events (created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_user_id ON audit_events (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events (action)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                idem_key TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_unique ON idempotency_responses (user_id, method, path, idem_key)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at ON idempotency_responses (expires_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                fail_count INTEGER NOT NULL DEFAULT 0,
                first_failed_at TEXT,
                last_failed_at TEXT,
                locked_until TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_failures_locked_until ON login_failures (locked_until)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS membership_plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price_cny REAL NOT NULL,
                currency TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_membership_plans_active ON membership_plans (active)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                plan_code TEXT NOT NULL,
                amount_cny REAL NOT NULL,
                currency TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                provider_txn_id TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_user_id ON payment_orders (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_created_at ON payment_orders (created_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                material TEXT NOT NULL,
                color TEXT,
                quantity INTEGER NOT NULL DEFAULT 1,
                volume_cm3 REAL,
                weight_g REAL,
                estimated_time_h REAL,
                cost_cny REAL,
                dimensions TEXT,
                status TEXT NOT NULL DEFAULT 'success',
                error_msg TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_history_user_id ON quote_history (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_history_created_at ON quote_history (created_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_state (
                rate_key TEXT PRIMARY KEY,
                bucket_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        plans_count = conn.execute("SELECT COUNT(*) AS c FROM membership_plans").fetchone()
        if not plans_count or int(plans_count["c"] or 0) == 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO membership_plans (code, name, price_cny, currency, duration_days, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("member_month", "会员（月）", 99.0, "CNY", 30, now_iso),
            )
            conn.execute(
                "INSERT OR IGNORE INTO membership_plans (code, name, price_cny, currency, duration_days, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("member_year", "会员（年）", 999.0, "CNY", 365, now_iso),
            )
        conn.commit()


def _safe_add_column(conn, table: str, column: str, col_type: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass


def get_app_defaults() -> dict:
    with get_db_conn() as conn:
        row = conn.execute("SELECT value_json FROM app_defaults WHERE key = ?", (APP_DEFAULTS_KEY,)).fetchone()
    if not row or not row["value_json"]:
        return {
            "materials": list(DEFAULT_MATERIALS),
            "colors": list(DEFAULT_COLORS),
            "pricing_config": dict(DEFAULT_PRICING_CONFIG),
        }
    raw = json.loads(row["value_json"])
    raw_materials = raw.get("materials")
    colors = raw.get("colors")
    raw_pricing = raw.get("pricing_config")
    materials = normalize_materials(raw_materials, fallback_colors=(colors or DEFAULT_COLORS)) if isinstance(raw_materials, list) else DEFAULT_MATERIALS
    derived_colors: list[str] = []
    for m in materials:
        for c in m.get("colors", []):
            if c not in derived_colors:
                derived_colors.append(c)
    pricing = merge_pricing_config(raw_pricing) if isinstance(raw_pricing, dict) else dict(DEFAULT_PRICING_CONFIG)
    return {"materials": materials, "colors": derived_colors or (colors or list(DEFAULT_COLORS)), "pricing_config": pricing}


def merge_pricing_config(raw_config):
    if not raw_config:
        return dict(DEFAULT_PRICING_CONFIG)
    merged = dict(DEFAULT_PRICING_CONFIG)
    for k, v in raw_config.items():
        merged[k] = v
    return merged
