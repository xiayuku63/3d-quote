"""SQLAlchemy ORM models — mirrors existing sqlite3 schema.

These models coexist with the raw SQL pathway. New code should
use these models + get_session(); existing code continues unchanged.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    materials = Column(Text)
    colors = Column(Text)
    pricing_config = Column(Text)
    email = Column(String)
    phone = Column(String)
    email_verified = Column(Integer, default=0)
    phone_verified = Column(Integer, default=0)
    membership_level = Column(String, default="free")
    membership_expires_at = Column(String)
    terms_accepted_at = Column(String)
    privacy_accepted_at = Column(String)
    terms_version = Column(String)
    privacy_version = Column(String)


class SlicerPreset(Base):
    __tablename__ = "slicer_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    ext = Column(String, nullable=False)
    content_b64 = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "name"),
    )


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String, nullable=False)
    target = Column(String, nullable=False)
    code_hash = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    used_at = Column(String)
    attempts = Column(Integer, default=0)

    __table_args__ = (
        Index("idx_vc_target", "channel", "target"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String, nullable=False, index=True)
    user_id = Column(Integer)
    username = Column(String)
    action = Column(String, nullable=False, index=True)
    ip = Column(String)
    method = Column(String)
    path = Column(String)
    request_id = Column(String)
    idempotency_key = Column(String)
    detail_json = Column(Text)


class QuoteHistory(Base):
    __tablename__ = "quote_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    filename = Column(String, nullable=False)
    material = Column(String, nullable=False)
    color = Column(String)
    quantity = Column(Integer, default=1)
    volume_cm3 = Column(Float)
    weight_g = Column(Float)
    estimated_time_h = Column(Float)
    cost_cny = Column(Float)
    dimensions = Column(String)
    status = Column(String, default="success")
    error_msg = Column(Text)
    created_at = Column(String, index=True)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, nullable=False, index=True)
    plan_code = Column(String, nullable=False)
    amount_cny = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    created_at = Column(String, index=True)
    paid_at = Column(String)
    provider_txn_id = Column(String)
    raw_json = Column(Text)


class MembershipPlan(Base):
    __tablename__ = "membership_plans"

    code = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    price_cny = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    duration_days = Column(Integer, nullable=False)
    active = Column(Integer, default=1)

    __table_args__ = (
        Index("idx_mp_active", "active"),
    )


class LoginFailure(Base):
    __tablename__ = "login_failures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String)
    key_hash = Column(String, unique=True, nullable=False)
    fail_count = Column(Integer, default=0)
    first_failed_at = Column(String)
    last_failed_at = Column(String)
    locked_until = Column(String)


class IdempotencyResponse(Base):
    __tablename__ = "idempotency_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(String)
    expires_at = Column(String, index=True)
    user_id = Column(Integer, nullable=False)
    method = Column(String, nullable=False)
    path = Column(String, nullable=False)
    idem_key = Column(String, nullable=False)
    status_code = Column(Integer)
    response_json = Column(Text)

    __table_args__ = (
        UniqueConstraint("user_id", "method", "path", "idem_key"),
    )


class AppDefault(Base):
    __tablename__ = "app_defaults"

    key = Column(String, primary_key=True)
    value_json = Column(Text, nullable=False)
    updated_at = Column(String)
    updated_by = Column(Integer)
    updated_by_username = Column(String)


class RateLimitState(Base):
    __tablename__ = "rate_limit_state"

    rate_key = Column(String, primary_key=True)
    bucket_json = Column(Text, nullable=False)
    updated_at = Column(String)
