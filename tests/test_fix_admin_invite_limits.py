"""Invitations can't be used to send unlimited email, or to carry links."""

import pytest
from limits import parse as parse_rate_limit

from orderflow import create_app
from orderflow.extensions import db
from orderflow.mail import outbox
from orderflow.models import User
from orderflow.services import clients as client_accounts
from orderflow.services.accounts import find_user_by_email, invite_user
from tests import factories
from tests.conftest import FreshContextClient


@pytest.fixture
def app():
    """The real rate limits, as in production."""
    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def browser_for(app, user):
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    assert response.status_code == 302
    return browser


def client_invite(browser, n, **changes):
    data = {"first_name": "Ravi", "last_name": "K", "buyer_company": "Ravi Stores", "email": f"ravi{n}@stores.example"}
    return browser.post("/app/clients/invite", data={**data, **changes})


def team_invite(browser, n):
    return browser.post("/app/team/invite", data={"first_name": "Deepa", "email": f"deepa{n}@kaveri.example"})


def text(response):
    return response.get_data(as_text=True)


# The daily allowance ----------------------------------------------------------------


def test_a_workspace_can_send_30_invitations_a_day(app, company, owner, mail):
    senders = [owner] + [factories.make_admin(company, email=f"ops{n}@kaveri.example") for n in range(3)]
    browsers = [browser_for(app, person) for person in senders]
    sent = 0
    for browser in browsers[:3]:
        for _ in range(10):
            assert client_invite(browser, sent).status_code == 302
            sent += 1
    assert len(mail) == 30

    response = client_invite(browsers[3], 99)
    assert response.status_code == 200
    assert "Your workspace has sent its 30 invitation emails for today. You can send more after " in text(response)
    assert find_user_by_email("ravi99@stores.example") is None
    assert len(mail) == 30

    # Team invitations and resent invitations share the allowance
    response = team_invite(browsers[0], 1)
    assert response.status_code == 200 and "sent its 30 invitation emails for today" in text(response)
    assert find_user_by_email("deepa1@kaveri.example") is None
    invited = find_user_by_email("ravi0@stores.example")
    invited.invite_sent_at = None
    db.session.commit()
    response = browsers[1].post(f"/app/clients/{invited.id}/resend-invite", follow_redirects=True)
    assert "sent its 30 invitation emails for today" in text(response)
    assert len(mail) == 30


def test_the_allowance_is_per_workspace(app, owner, other_company, mail, monkeypatch):
    monkeypatch.setattr(client_accounts, "_DAILY_INVITATIONS", parse_rate_limit("1 per day"))
    assert client_invite(browser_for(app, owner), 1).status_code == 302
    assert client_invite(browser_for(app, owner), 2).status_code == 200
    assert client_invite(browser_for(app, other_company["owner"]), 3).status_code == 302
    assert len(mail) == 2


def test_changing_a_pending_clients_email_uses_the_allowance(app, company, owner, mail, monkeypatch):
    monkeypatch.setattr(client_accounts, "_DAILY_INVITATIONS", parse_rate_limit("2 per day"))
    browser = browser_for(app, owner)
    assert client_invite(browser, 1).status_code == 302
    invited = find_user_by_email("ravi1@stores.example")
    edit = {"first_name": "Ravi", "buyer_company": "Ravi Stores"}
    assert (
        browser.post(f"/app/clients/{invited.id}/edit", data={**edit, "email": "r2@stores.example"}).status_code == 302
    )
    response = browser.post(f"/app/clients/{invited.id}/edit", data={**edit, "email": "r3@stores.example"})
    assert response.status_code == 200 and "sent its 2 invitation emails for today" in text(response)
    assert db.session.get(User, invited.id).email == "r2@stores.example"
    assert len(mail) == 2
    # Edits that don't send anything still work
    response = browser.post(
        f"/app/clients/{invited.id}/edit", data={**edit, "email": "r2@stores.example", "phone": "+91 98450 12345"}
    )
    assert response.status_code == 302 and db.session.get(User, invited.id).phone == "+91 98450 12345"


def test_emails_that_fail_to_send_dont_use_the_allowance(app, owner, mail, monkeypatch):
    monkeypatch.setattr(client_accounts, "_DAILY_INVITATIONS", parse_rate_limit("1 per day"))
    browser = browser_for(app, owner)
    app.config["MAIL_BACKEND"] = "not-a-backend"
    assert client_invite(browser, 1).status_code == 302  # added, but the email couldn't be sent
    app.config["MAIL_BACKEND"] = "memory"
    assert client_invite(browser, 2).status_code == 302
    assert [message.to for message in outbox()] == [["ravi2@stores.example"]]


def test_a_broken_limit_store_doesnt_block_invitations(app, owner, mail, monkeypatch):
    class Broken:
        def __getattr__(self, name):
            raise ConnectionError("limit store unavailable")

    monkeypatch.setattr(type(client_accounts.limiter), "limiter", property(lambda self: Broken()))
    assert client_accounts.invitations_allowed(owner.company_id)
    client_accounts.count_invitation(owner.company_id)
    assert "You can send more tomorrow" in client_accounts.invitation_limit_message(owner.company)


# Per-person limits on each action -------------------------------------------------------


def test_one_person_can_send_10_client_invitations_a_minute(app, company, owner, admin, mail):
    browser = browser_for(app, owner)
    for n in range(10):
        assert client_invite(browser, n).status_code == 302
    response = client_invite(browser, 10)
    assert response.status_code == 429 and "Too many attempts" in text(response)
    assert find_user_by_email("ravi10@stores.example") is None
    assert browser.get("/app/clients/invite").status_code == 200  # only sending is limited
    assert client_invite(browser_for(app, admin), 11).status_code == 302  # someone else can still invite


@pytest.mark.parametrize("action", ["client_resend", "team_resend", "team_invite"])
def test_resend_and_team_invite_are_limited_per_minute(app, company, owner, mail, action):
    browser = browser_for(app, owner)
    pending_client = factories.make_client(company, email="p@shop.example", password=None, verified=False)
    pending_admin = factories.make_admin(company, email="p@kaveri.example", password=None, verified=False)
    send = {
        "client_resend": lambda n: browser.post(f"/app/clients/{pending_client.id}/resend-invite"),
        "team_resend": lambda n: browser.post(f"/app/team/{pending_admin.id}/resend-invite"),
        "team_invite": lambda n: team_invite(browser, n),
    }[action]
    assert [send(n).status_code for n in range(11)] == [302] * 10 + [429]


@pytest.mark.parametrize(
    "path", ["/app/clients/invite", "/app/team/invite", "/app/clients/1/edit", "/app/clients/1/resend-invite"]
)
def test_visitors_are_still_sent_to_sign_in(app, client, buyer, path):
    response = client.post(path, data={"email": "x@example.com"})
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")


# No links or markup in what the emails say ------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("buyer_company", "Act now: http://evil.example/pay", "The business name can&#39;t include a web address"),
        ("buyer_company", "Pay at pay-customs.co.in today", "The business name can&#39;t include a web address"),
        ("first_name", "www.evil-site", "A name can&#39;t include a web address"),
        ("first_name", "<b>Ravi</b>", "A name can&#39;t include a web address or the symbols &lt; and &gt;."),
        ("last_name", "visit refunds.com", "A name can&#39;t include a web address"),
    ],
)
def test_invitation_names_must_be_plain_text(app, owner, mail, field, value, message):
    response = client_invite(browser_for(app, owner), 1, **{field: value})
    assert response.status_code == 200 and message in text(response)
    assert find_user_by_email("ravi1@stores.example") is None and mail == []


@pytest.mark.parametrize(
    "name",
    [
        "St. Mary's Stores",
        "Dr.Reddy's Pharma",
        "Mehta & Co.",
        "A.B.C. Traders",
        "Sri Sai Pvt.Ltd",
        "No.1 Kirana",
        "D'Souza",
    ],
)
def test_ordinary_business_names_are_fine(name):
    assert not client_accounts.has_link_or_markup(name)


@pytest.mark.parametrize(
    "name",
    ["https://x.example", "HTTP://X", "www.x.example", "pay.com", "x.co.in/refund", "Refunds.IN", "a<b", "a>b"],
)
def test_links_and_markup_are_spotted(name):
    assert client_accounts.has_link_or_markup(name)


def test_team_invite_names_must_be_plain_text(app, owner, mail):
    response = browser_for(app, owner).post(
        "/app/team/invite", data={"first_name": "Click https://evil.example", "email": "d@kaveri.example"}
    )
    assert response.status_code == 200 and "A name can&#39;t include a web address" in text(response)
    assert mail == []


def test_company_and_profile_names_must_be_plain_text(app, company, owner):
    browser = browser_for(app, owner)
    settings = {"name": "Customs fee at pay-customs.in", "timezone": "Asia/Kolkata", "order_prefix": "ORD"}
    response = browser.post("/app/settings", data=settings)
    assert response.status_code == 200 and "The company name can&#39;t include a web address" in text(response)
    response = browser.post("/app/settings/profile", data={"first_name": "Priya", "last_name": "see bit.ly"})
    assert response.status_code == 200 and "A name can&#39;t include a web address" in text(response)
    db.session.expire_all()
    assert company.name == "Kaveri Supplies" and owner.last_name == "Test"


def test_a_company_name_with_a_link_from_sign_up_blocks_invitations(app, company, owner, mail):
    company.name = "Your parcel is held - pay at http://evil.example"
    db.session.commit()
    browser = browser_for(app, owner)
    response = client_invite(browser, 1)
    assert response.status_code == 200
    assert "Your company name in Settings includes a web address" in text(response)
    response = team_invite(browser, 1)
    assert response.status_code == 200 and "Your company name in Settings includes a web address" in text(response)
    assert mail == [] and find_user_by_email("ravi1@stores.example") is None


def test_an_inviter_name_with_a_link_blocks_invitations(app, company, owner, mail):
    owner.last_name = "at www.evil.example"
    db.session.commit()
    response = client_invite(browser_for(app, owner), 1)
    assert response.status_code == 200 and "The inviter&#39;s name" in text(response)
    assert mail == []


def test_resending_an_old_invitation_with_a_link_is_refused(app, company, owner, mail):
    old = factories.make_client(
        company, email="old@shop.example", buyer_company="Claim at prize.com", password=None, verified=False
    )
    response = browser_for(app, owner).post(f"/app/clients/{old.id}/resend-invite", follow_redirects=True)
    assert "has a web address or the symbols &lt; and &gt; in their name or business name" in text(response)
    assert mail == []


def test_names_in_invitation_emails_are_escaped(app, owner, mail):
    invite_user(owner.company, owner, "x@shop.example", "client", "Ravi", buyer_company="<a href='x'>Ravi & Sons</a>")
    html = mail[-1].html
    assert "<a href='x'>" not in html and "&lt;a href=&#39;x&#39;&gt;Ravi &amp; Sons&lt;/a&gt;" in html
