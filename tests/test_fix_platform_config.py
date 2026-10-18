"""Start-up settings: what the app refuses to run with, and what it warns about."""

import pytest

from orderflow import config, create_app

POSTGRES = "postgresql+psycopg://orderflow:secret@db.internal:5432/orderflow"


@pytest.fixture
def under_gunicorn(monkeypatch):
    """Run as a WSGI server would: no APP_ENV, outside the flask command."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("FLASK_RUN_FROM_CLI", raising=False)


@pytest.fixture
def flask_command(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")


# APP_ENV and the development key -------------------------------------------


def test_a_server_without_app_env_refuses_to_start(under_gunicorn):
    with pytest.raises(RuntimeError, match="APP_ENV is not set"):
        config.get_config()
    with pytest.raises(RuntimeError, match="APP_ENV is not set"):
        create_app()


def test_the_flask_command_still_defaults_to_development(flask_command):
    assert config.get_config() is config.DevelopmentConfig
    app = create_app(SQLALCHEMY_DATABASE_URI="sqlite://")
    assert app.debug


def test_an_explicit_app_env_is_used(under_gunicorn, monkeypatch):
    monkeypatch.setenv("APP_ENV", " Production ")
    assert config.get_config() is config.ProductionConfig
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="Unknown APP_ENV"):
        config.get_config()


def test_the_public_development_key_only_works_in_the_flask_command(under_gunicorn, monkeypatch):
    monkeypatch.setattr(config.DevelopmentConfig, "SECRET_KEY", config.DEV_SECRET_KEY)
    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        create_app("development", SQLALCHEMY_DATABASE_URI="sqlite://")

    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    assert create_app("development", SQLALCHEMY_DATABASE_URI="sqlite://").secret_key == config.DEV_SECRET_KEY


def test_development_settings_on_a_server_are_logged_loudly(under_gunicorn, monkeypatch, caplog):
    monkeypatch.setattr(config.DevelopmentConfig, "SECRET_KEY", "a real secret from the environment")
    create_app("development", SQLALCHEMY_DATABASE_URI="sqlite://")
    assert "APP_ENV is development outside the flask command" in caplog.text


def test_testing_and_the_flask_command_are_quiet(flask_command, monkeypatch, caplog):
    monkeypatch.setattr(config.DevelopmentConfig, "SECRET_KEY", config.DEV_SECRET_KEY)
    create_app("development", SQLALCHEMY_DATABASE_URI="sqlite://")
    create_app("testing")
    assert "APP_ENV is development" not in caplog.text


# DATABASE_URL in production -------------------------------------------------


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", "a real secret from the environment")
    monkeypatch.setattr(config.ProductionConfig, "APP_BASE_URL", "https://orders.example.com")
    monkeypatch.setattr(config.ProductionConfig, "MAIL_FROM", "OrderFlow <orders@example.com>")
    monkeypatch.setattr(config.ProductionConfig, "RESEND_API_KEY", "re_test")


def test_production_without_database_url_refuses_to_start(production, monkeypatch):
    # What DATABASE_URL being unset gives: the local SQLite file
    monkeypatch.setattr(config.ProductionConfig, "SQLALCHEMY_DATABASE_URI", config.local_database_url())
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        create_app("production")


def test_database_url_defaults_to_the_local_file_only_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.database_url() == config.local_database_url()
    monkeypatch.setenv("DATABASE_URL", "  ")
    assert config.database_url() == config.local_database_url()
    monkeypatch.setenv("DATABASE_URL", "postgres://orderflow:secret@db.internal:5432/orderflow")
    assert config.database_url() == POSTGRES


def test_production_with_postgres_starts_quietly(production, monkeypatch, caplog):
    monkeypatch.setattr(config.ProductionConfig, "SQLALCHEMY_DATABASE_URI", POSTGRES)
    app = create_app("production")
    assert app.config["SQLALCHEMY_DATABASE_URI"] == POSTGRES
    assert "SQLite" not in caplog.text and "DATABASE_URL" not in caplog.text


def test_production_on_an_explicit_sqlite_file_is_warned_about(production, caplog):
    create_app("production", SQLALCHEMY_DATABASE_URI="sqlite:////var/data/orderflow.db")
    assert "The database is SQLite" in caplog.text
