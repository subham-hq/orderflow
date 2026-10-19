import pytest

from orderflow import create_app
from orderflow.extensions import db


def test_security_headers_on_every_page(client):
    response = client.get("/")
    headers = response.headers
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_hsts_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/").headers
    assert "Strict-Transport-Security" in client.get("/", base_url="https://localhost").headers


def test_signed_in_pages_are_not_cached(client, login, owner):
    login(owner)
    assert client.get("/app/").headers["Cache-Control"] == "no-store"


def test_session_cookie_flags(client, owner):
    response = client.post("/login", data={"email": owner.email, "password": "a long test password"})
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_nul_bytes_are_rejected(client):
    assert client.post("/login", data={"email": "a\x00@example.com", "password": "x"}).status_code == 400
    assert client.get("/?q=%00").status_code == 400


def test_csrf_is_enforced_when_enabled():
    app = create_app("testing", WTF_CSRF_ENABLED=True)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        response = client.post("/login", data={"email": "a@example.com", "password": "whatever"})
        assert response.status_code == 400
        assert "This form has expired" in response.get_data(as_text=True)
        db.drop_all()


def test_friendly_404(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "couldn&#39;t find that page" in response.get_data(as_text=True)


def test_health_check_and_robots(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}
    assert "Disallow: /app/" in client.get("/robots.txt").get_data(as_text=True)


def test_production_requires_secret_key(monkeypatch):
    from orderflow import config

    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError):
        create_app("production")


def test_postgres_urls_are_rewritten_for_psycopg(monkeypatch):
    from orderflow.config import database_url

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert database_url() == "postgresql+psycopg://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    assert database_url() == "postgresql+psycopg://u:p@host/db"


def test_staff_pages_are_closed_to_clients_and_anonymous_users(client, login, buyer):
    assert client.get("/app/").headers["Location"].startswith("/login")
    login(buyer)
    assert client.get("/app/").headers["Location"] == "/portal/"


def test_portal_is_closed_to_staff(client, login, owner):
    login(owner)
    assert client.get("/portal/").headers["Location"] == "/app/"


def test_team_page_is_owner_only(client, login, admin):
    login(admin)
    assert client.get("/app/team").headers["Location"] == "/app/"


def test_proxy_headers_are_trusted_only_from_the_proxy():
    app = create_app("testing", TRUST_PROXY=True)
    with app.app_context():
        db.create_all()

        @app.get("/_ip")
        def show_ip():
            from flask import request

            return f"{request.remote_addr} {request.scheme}"

        client = app.test_client()
        # A client-supplied value comes first; the proxy appends the real address last
        response = client.get("/_ip", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9", "X-Forwarded-Proto": "https"})
        assert response.get_data(as_text=True) == "203.0.113.9 https"
        db.drop_all()


def test_production_settings_warnings(monkeypatch, caplog):
    from orderflow import config

    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", "x")
    monkeypatch.setattr(config.ProductionConfig, "APP_BASE_URL", "http://localhost:5000")
    monkeypatch.setattr(config.ProductionConfig, "RESEND_API_KEY", None)
    monkeypatch.setattr(config.ProductionConfig, "MAIL_BACKEND", "resend")
    create_app("production", SQLALCHEMY_DATABASE_URI="sqlite://")
    assert "APP_BASE_URL" in caplog.text
    assert "RESEND_API_KEY" in caplog.text
    assert "MAIL_FROM" in caplog.text
