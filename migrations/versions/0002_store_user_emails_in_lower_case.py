"""Store user emails in lower case

Sign-in now looks addresses up exactly (so the unique index on users.email
is used) instead of comparing lower(email), which scanned the whole table.
Every address the app writes is already trimmed and lower case; this fixes
any row written otherwise (for example by `flask seed-demo --email` with
capitals). A row whose lower-case form would clash with another account is
left alone rather than failing the upgrade.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 20:37:51.347826

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            "UPDATE users SET email = lower(trim(email)) "
            "WHERE email <> lower(trim(email)) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM users AS other "
            "WHERE other.id <> users.id AND lower(trim(other.email)) = lower(trim(users.email))"
            ")"
        )
    )


def downgrade():
    # Nothing to undo: lower-case addresses work with the old lookup too.
    pass
