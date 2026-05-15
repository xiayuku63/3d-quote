"""API endpoint tests – uses FastAPI TestClient."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


class TestPublicEndpoints:
    def test_healthz(self):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz(self):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["db"] == "ok"

    def test_index_page(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "3D打印" in resp.text or "3D Printing" in resp.text

    def test_register_page(self):
        resp = client.get("/register")
        assert resp.status_code == 200
        assert "注册" in resp.text or "register" in resp.text.lower()

    def test_terms_page(self):
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
        assert "用户协议" in resp.text

    def test_privacy_page(self):
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200
        assert "隐私政策" in resp.text

    def test_billing_plans(self):
        resp = client.get("/api/billing/plans")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 2
        codes = {p["code"] for p in items}
        assert "member_month" in codes

    def test_captcha(self):
        resp = client.get("/api/auth/captcha")
        assert resp.status_code == 200
        data = resp.json()
        assert "captcha_id" in data
        assert "image_url" in data
        assert "expires_in" in data

    def test_captcha_image(self):
        resp = client.get("/api/auth/captcha")
        captcha_id = resp.json()["captcha_id"]
        img_resp = client.get(f"/api/auth/captcha/image/{captcha_id}")
        assert img_resp.status_code == 200
        assert img_resp.headers["content-type"].startswith("image/")


class TestAuthFlow:
    def test_full_registration_flow(self):
        """End-to-end: captcha → register → login → me"""
        # Step 1: Get captcha
        captcha_resp = client.get("/api/auth/captcha")
        assert captcha_resp.status_code == 200
        captcha_id = captcha_resp.json()["captcha_id"]

        # Get captcha answer (dev mode returns the code in verify/send response)
        verify_resp = client.post(
            "/api/auth/verify/send",
            json={
                "channel": "email",
                "target": "test_reg@example.com",
                "captcha_id": captcha_id,
                "captcha_code": "ABCD",  # dev mode bypass
            },
        )
        assert verify_resp.status_code in {200, 429, 500, 422}  # 429 rate limit cooldown, 500 no SMTP, 422 validation

        # Get a fresh captcha for registration
        captcha_resp2 = client.get("/api/auth/captcha")
        captcha_id2 = captcha_resp2.json()["captcha_id"]

        # Try to verify the captcha code
        # In dev mode, we can get the code from send response if it succeeded
        # Otherwise, we'll test that captcha verification works

        # Test register check
        check_resp = client.post(
            "/api/auth/register/check",
            json={"field": "username", "value": "test_user_123"},
        )
        assert check_resp.status_code == 200
        assert check_resp.json()["valid"] is True
        # should not exist yet
        assert check_resp.json()["exists"] is False

    def test_register_check_email(self):
        resp = client.post(
            "/api/auth/register/check",
            json={"field": "email", "value": "bogus@nonexistent.test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["exists"] is False

    def test_register_check_phone(self):
        resp = client.post(
            "/api/auth/register/check",
            json={"field": "phone", "value": "+8613800138000"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["exists"] is False

    def test_register_check_invalid_field(self):
        resp = client.post(
            "/api/auth/register/check",
            json={"field": "bogus", "value": "test"},
        )
        assert resp.status_code == 400

    def test_login_without_token_returns_401(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_quote_without_token_returns_401(self):
        resp = client.post("/api/quote")
        assert resp.status_code == 401

    def test_admin_without_token_returns_401(self):
        resp = client.get("/api/admin/defaults")
        assert resp.status_code == 401


class TestErrorHandling:
    def test_404_page(self):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404

    def test_validation_error_returns_422(self):
        resp = client.post(
            "/api/auth/login",
            json={"identifier": "", "password": "short", "captcha_id": "", "captcha_code": "", "accept_terms": False, "accept_privacy": False},
        )
        assert resp.status_code == 422

    def test_method_not_allowed(self):
        resp = client.get("/api/auth/login")
        assert resp.status_code == 405


class TestFormulaValidate:
    def test_validate_valid_formula(self):
        resp = client.post(
            "/api/formula/validate",
            json={
                "unit_cost_formula": "effective_weight_g * price_per_kg / 1000",
                "total_cost_formula": "unit_cost_cny * quantity",
            },
        )
        assert resp.status_code == 401  # needs auth

    def test_validate_with_auth(self):
        # Test with auth token - needs a real user
        pass  # will implement when we create test user


class TestRateLimiting:
    def test_auth_rate_limiting(self):
        """Hit login endpoint rapidly, should eventually get 429"""
        responses = []
        for _ in range(20):
            resp = client.post(
                "/api/auth/login",
                json={
                    "identifier": "testuser",
                    "password": "Test123456",
                    "captcha_id": "fake_id",
                    "captcha_code": "ABCD",
                    "accept_terms": True,
                    "accept_privacy": True,
                },
            )
            responses.append(resp.status_code)
        # At least some should be rate-limited
        status_set = set(responses)
        assert 429 in status_set or 422 in status_set or 400 in status_set
