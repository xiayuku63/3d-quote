"""Tests for Phase 2B/2C new features: enhanced health, backup, quote history, drag-drop JS."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from main import app
from app import backup as backup_mod

client = TestClient(app)


class TestEnhancedHealth:
    def test_healthz_has_uptime(self):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime_seconds" in data
        assert "disk_free_mb" in data
        assert data["uptime_seconds"] >= 0

    def test_readyz_has_user_count(self):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert "user_count" in data
        assert "uptime_seconds" in data
        assert data["db"] == "ok"


class TestBackup:
    def test_backup_list_unauthorized(self):
        resp = client.get("/api/admin/maintenance/backup")
        assert resp.status_code == 401

    def test_backup_create_unauthorized(self):
        resp = client.post("/api/admin/maintenance/backup")
        assert resp.status_code == 401

    def test_backup_module_structure(self):
        """Ensure backup module has expected functions."""
        assert hasattr(backup_mod, "create_backup")
        assert hasattr(backup_mod, "list_backups")
        assert hasattr(backup_mod, "cleanup_old_backups")


class TestQuoteHistory:
    def test_history_unauthorized(self):
        resp = client.get("/api/quote/history")
        assert resp.status_code == 401


class TestStaticFiles:
    def test_main_js_served(self):
        resp = client.get("/static/js/main.js")
        assert resp.status_code == 200
        content = resp.text
        assert "THREE" in content or "quoteSelectedFiles" in content
        # Check drag-drop code exists
        assert "dragenter" in content or "dropZone" in content

    def test_register_js_served(self):
        resp = client.get("/static/js/register.js")
        assert resp.status_code == 200

    def test_admin_users_js_served(self):
        resp = client.get("/static/js/admin_users.js")
        assert resp.status_code == 200

    def test_drop_zone_in_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "drop-zone" in resp.text

    def test_history_section_in_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "history-container" in resp.text or "报价历史" in resp.text


class TestLogging:
    def test_logging_module_imports(self):
        from app.logging_config import setup_logging, log_request, log_event
        logger = setup_logging()
        assert logger is not None
        assert logger.handlers  # should have handlers

    def test_logs_dir_created(self):
        logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
        assert os.path.isdir(logs_dir)


class TestRateLimitRecovery:
    def test_healthz_always_accessible(self):
        """healthz should never be rate-limited."""
        for _ in range(5):
            resp = client.get("/healthz")
            assert resp.status_code == 200


class TestPasswordReset:
    def test_reset_request_requires_captcha(self):
        resp = client.post("/api/auth/password/reset/request", json={
            "email": "test@example.com",
            "captcha_id": "fake",
            "captcha_code": "ABCD",
        })
        # Either 400 (bad captcha) or 422 (validation) 
        assert resp.status_code in {400, 422}

    def test_reset_confirm_invalid_code(self):
        resp = client.post("/api/auth/password/reset/confirm", json={
            "email": "test@example.com",
            "code": "000000",
            "new_password": "NewTest123",
        })
        assert resp.status_code in {400, 422}


class TestConcurrencyConfig:
    def test_quote_concurrency_env(self):
        from app.config import QUOTE_CONCURRENCY
        assert QUOTE_CONCURRENCY >= 1


class TestErrorNotifier:
    def test_notifier_exists(self):
        from app.error_notify import error_notifier, notify_critical
        assert error_notifier is not None
        assert callable(notify_critical)
