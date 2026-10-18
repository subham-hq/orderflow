"""Emails are stored in lower case, so sign-in finds them through the unique index and still ignores case."""

import os
from contextlib import contextmanager

import pytest
from flask_migrate import upgrade
from sqlalchemy import event, text

from orderflow import create_app
from orderflow.extensions import db
from orderflow.models import User
from orderflow.services.accounts import find_user_by_email
from tests import factories


@contextmanager
def _statements():
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


def test_emails_are_stored_trimmed_and_in_lower_case(company):
    user = User(company=company, email="  Priya.Nair@Kaveri.EXAMPLE ", first_name="Priya", role="owner")
    db.session.add(user)
    db.session.commit()
    assert db.session.scalar(db.select(User.email)) == "priya.nair@kaveri.example"


def test_lookup_compares_the_address_exactly(owner):
    with _statements() as seen:
        assert find_user_by_email("  OWNER@Kaveri.Example ") == owner
    [query] = [statement for statement, _ in seen if "FROM users" in statement]
    assert "lower(" not in query.lower()
    assert "users.email = " in query


def test_sign_in_still_ignores_case_for_an_address_typed_with_capitals(client, company):
    factories.make_owner(company, email="Ravi.Kumar@Example.COM")
    for typed in ["ravi.kumar@example.com", "RAVI.KUMAR@EXAMPLE.COM", " Ravi.Kumar@Example.com "]:
        response = client.post("/login", data={"email": typed, "password": factories.PASSWORD})
        assert response.headers["Location"] == "/app/"
        client.post("/logout")


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="Postgres plans")
def test_postgres_uses_the_unique_index_for_sign_in(owner):
    email = owner.email  # load it first, so only the lookup itself is recorded
    with _statements() as seen:
        find_user_by_email(email)
    [(query, parameters)] = [(statement, params) for statement, params in seen if "FROM users" in statement]
    with db.engine.connect() as connection:
        connection.exec_driver_sql("SET enable_seqscan = off")
        plan = "\n".join(row[0] for row in connection.exec_driver_sql(f"EXPLAIN {query}", parameters))
    assert "ix_users_email" in plan, plan


def test_the_migration_lowers_addresses_saved_with_capitals(tmp_path):
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'emails.db'}")
    with app.app_context():
        upgrade(revision="0001")
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, name, timezone, order_prefix, next_order_seq, created_at) "
                    "VALUES (1, 'Kaveri', 'Asia/Kolkata', 'ORD', 1, CURRENT_TIMESTAMP)"
                )
            )
            for user_id, email in [(1, "Owner@Kaveri.Example"), (2, "dup@kaveri.example"), (3, "DUP@kaveri.example")]:
                connection.execute(
                    text(
                        "INSERT INTO users (id, company_id, email, first_name, role, active, auth_version, created_at) "
                        "VALUES (:id, 1, :email, 'X', 'client', 1, 1, CURRENT_TIMESTAMP)"
                    ),
                    {"id": user_id, "email": email},
                )
        upgrade()
        with db.engine.connect() as connection:
            emails = dict(connection.execute(text("SELECT id, email FROM users")).all())
        # A clash with another account is left for a person to sort out, not failed on
        assert emails == {1: "owner@kaveri.example", 2: "dup@kaveri.example", 3: "DUP@kaveri.example"}
        db.engine.dispose()
