from datetime import timedelta

from orderflow.extensions import db
from orderflow.models import User
from orderflow.services import clients
from orderflow.timeutil import utcnow
from tests import factories


def test_account_status(app, company):
    joined = factories.make_client(company)
    invited = factories.make_client(company, password=None, verified=False)
    suspended = factories.make_client(company, active=False)
    suspended_invitee = factories.make_client(company, password=None, active=False)
    assert clients.account_status(joined) == clients.ACTIVE
    assert clients.account_status(invited) == clients.INVITED
    assert clients.account_status(suspended) == clients.SUSPENDED
    assert clients.account_status(suspended_invitee) == clients.SUSPENDED


def test_where_status_and_counts_agree(app, company, other_company):
    factories.make_client(company)
    factories.make_client(company)
    factories.make_client(company, password=None)
    factories.make_client(company, active=False)
    base = db.select(User).where(User.company_id == company.id, User.role == "client")
    counts = clients.status_counts(base)
    assert counts == {"": 4, "active": 2, "invited": 1, "suspended": 1}
    for status in clients.ACCOUNT_STATUSES:
        assert len(db.session.scalars(clients.where_status(base, status)).all()) == counts[status]
    assert len(db.session.scalars(clients.where_status(base, "")).all()) == 4


def test_display_name(app, company, owner):
    assert clients.display_name(factories.make_client(company, first_name="Ana", last_name="Rao")) == (
        "Ana Rao (Buyer Pvt Ltd)"
    )
    assert clients.display_name(factories.make_client(company, first_name="Ana", buyer_company=None)) == "Ana Test"
    assert clients.display_name(owner) == "Priya Test"


def test_set_active_ends_sessions_only_when_suspending(app, company):
    user = factories.make_client(company)
    version = user.auth_version
    assert clients.set_active(user, True) is False and user.auth_version == version
    assert clients.set_active(user, False) is True and user.auth_version == version + 1
    assert clients.set_active(user, False) is False and user.auth_version == version + 1
    assert clients.set_active(user, True) is True and user.active and user.auth_version == version + 1


def test_invite_recently_sent(app, company):
    now = utcnow()
    user = factories.make_client(company, password=None, invite_sent_at=None)
    assert clients.invite_recently_sent(user) is False
    user.invite_sent_at = now - timedelta(seconds=30)
    assert clients.invite_recently_sent(user, now) is True
    user.invite_sent_at = now - timedelta(minutes=2)
    assert clients.invite_recently_sent(user, now) is False


def test_order_counts_handles_no_clients(app, company):
    assert clients.order_counts(company.id, []) == {}
