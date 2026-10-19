"""The Alembic migrations must build exactly the schema the models describe."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from orderflow import create_app
from orderflow.extensions import db


def test_migrations_match_models(tmp_path):
    database = tmp_path / "migrations.db"
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{database}")
    with app.app_context():
        upgrade()
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, db.metadata)
        assert differences == []
        db.engine.dispose()
