"""Staff can change their own sign-in email, and a client's can be changed
after they've joined; either way the new address is confirmed by a link the
account's owner opens while signed in."""

from datetime import timedelta

import pytest
from itsdangerous import URLSafeTimedSerializer

from orderflow import create_app
from orderflow.extensions import db
from orderflow.models import User
from orderflow.services import clients as accounts
from orderflow.tokens import RESET_PASSWORD, make_token
from tests import factories
from tests.conftest import FreshContextClient, link_from
from tests.test_design_emails import check_layout


def text(response):
    return response.get_data(as_text=True)


def get_user(user_id):
    return db.session.get(User, user_id)


def browser_for(app, user, password=factories.PASSWORD):
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": password})
    assert response.status_code == 302, text(response)
    return browser


def can_sign_in(app, email):
    response = app.test_client().post("/login", data={"email": email, "password": factories.PASSWORD})
    return response.status_code == 302


def ask_for_new_email(browser, new_email, password=factories.PASSWORD):
    return browser.post("/app/settings/email", data={"new_email": new_email, "current_password": password})


# Staff: their own sign-in email ---------------------------------------------------------


def test_the_profile_page_offers_to_change_the_sign_in_email(app, owner):
    html = text(browser_for(app, owner).get("/app/settings/profile"))
    assert 'action="/app/settings/email"' in html
    assert 'name="new_email"' in html and 'name="current_password" required type="password"' in html
    assert "Send confirmation link" in html and "owner@kaveri.example" in html


def test_a_staff_email_changes_only_once_the_link_is_opened(app, owner, mail):
    browser = browser_for(app, owner)
    response = ask_for_new_email(browser, " Priya@NewDomain.example ")
    assert response.status_code == 302
    flashed = text(browser.get("/app/settings/profile"))
    assert "Check priya@newdomain.example for a link to confirm it." in flashed
    assert "keep signing in with owner@kaveri.example" in flashed

    [message] = mail
    assert message.to == ["priya@newdomain.example"]
    assert message.subject == "Confirm your new email for OrderFlow"
    html = check_layout(message)
    assert "you asked to sign in to the Kaveri Supplies workspace with this email address" in html.text
    assert get_user(owner.id).email == "owner@kaveri.example"  # nothing changes yet
    assert can_sign_in(app, "owner@kaveri.example")

    response = browser.get(link_from(message, "/app/email/confirm/"))
    assert response.status_code == 302 and response.headers["Location"] == "/app/"
    assert "Your sign-in email is now priya@newdomain.example." in text(browser.get("/app/"))
    changed = get_user(owner.id)
    assert changed.email == "priya@newdomain.example" and changed.email_verified_at is not None
    assert can_sign_in(app, "priya@newdomain.example") and not can_sign_in(app, "owner@kaveri.example")

    notice = mail[-1]
    assert notice.to == ["owner@kaveri.example"] and notice.subject == "Your OrderFlow sign-in email has changed"
    notice_html = check_layout(notice)
    assert "p***@newdomain.example" in notice_html.text and "priya@newdomain.example" not in notice.text
    assert "write to us at sales@orderflow.test" in notice_html.text


def test_the_password_is_needed_to_ask(app, owner, mail):
    response = ask_for_new_email(browser_for(app, owner), "new@kaveri.example", password="not my password")
    assert response.status_code == 200 and "That isn&#39;t your password." in text(response)
    assert mail == []


@pytest.mark.parametrize("new_email", ["OPS@Kaveri.example", "buyer@sunrise.example", "owner@kaveri.example"])
def test_an_address_in_use_is_refused(app, owner, admin, buyer, mail, new_email):
    response = ask_for_new_email(browser_for(app, owner), new_email)
    assert response.status_code == 200
    body = text(response)
    assert "already has an OrderFlow account" in body or "That&#39;s already the email you sign in with." in body
    assert mail == []


def test_a_bad_address_is_refused_and_the_profile_form_keeps_its_values(app, owner, mail):
    response = ask_for_new_email(browser_for(app, owner), "not-an-email")
    body = text(response)
    assert response.status_code == 200 and "Enter a valid email address" in body
    assert 'value="Priya"' in body  # the profile form above still shows the saved name
    assert mail == []


def test_a_link_only_works_for_the_account_it_was_sent_for(app, owner, admin, mail):
    ask_for_new_email(browser_for(app, owner), "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    response = browser_for(app, admin).get(link)
    assert response.status_code == 403 and "This link is for a different account." in text(response)
    assert get_user(owner.id).email == "owner@kaveri.example"


def test_a_signed_out_person_signs_in_first(app, owner, mail):
    ask_for_new_email(browser_for(app, owner), "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    visitor = app.test_client()
    response = visitor.get(link)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login?next=/app/email/confirm/")
    assert get_user(owner.id).email == "owner@kaveri.example"  # opening it signed out changes nothing

    response = visitor.post(response.headers["Location"], data={"email": owner.email, "password": factories.PASSWORD})
    assert response.headers["Location"] == link
    visitor.get(link)
    assert get_user(owner.id).email == "priya@newdomain.example"


def test_a_link_works_once(app, owner, mail):
    browser = browser_for(app, owner)
    ask_for_new_email(browser, "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    browser.get(link)
    response = browser.get(link)
    assert response.status_code == 400 and "already been used or is no longer valid" in text(response)


def test_a_second_request_replaces_the_first_once_either_is_used(app, owner, mail):
    browser = browser_for(app, owner)
    ask_for_new_email(browser, "first@newdomain.example")
    ask_for_new_email(browser, "second@newdomain.example")
    first, second = (link_from(message, "/app/email/confirm/") for message in mail[-2:])
    browser.get(second)
    assert browser.get(first).status_code == 400
    assert get_user(owner.id).email == "second@newdomain.example"


def test_changing_the_password_cancels_a_pending_link(app, owner, mail):
    browser = browser_for(app, owner)
    ask_for_new_email(browser, "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    user = get_user(owner.id)
    user.end_all_sessions()  # as a password change does
    db.session.commit()
    response = browser_for(app, owner).get(link)
    assert response.status_code == 400 and get_user(owner.id).email == "owner@kaveri.example"


def test_an_expired_link_is_refused(app, owner, mail, monkeypatch):
    browser = browser_for(app, owner)
    ask_for_new_email(browser, "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    app.config["VERIFY_TOKEN_MAX_AGE"] = timedelta(seconds=-1)
    response = browser.get(link)
    assert response.status_code == 400 and "This link has expired." in text(response)
    assert "You can ask for a new one from your profile." in text(response)


@pytest.mark.parametrize("token", ["junk", "a.b.c"])
def test_a_tampered_link_is_refused(app, owner, token):
    response = browser_for(app, owner).get(f"/app/email/confirm/{token}")
    assert response.status_code == 400 and "This link is not valid." in text(response)


def test_a_link_signed_for_another_purpose_is_refused(app, owner):
    reset = make_token(RESET_PASSWORD, owner)
    assert browser_for(app, owner).get(f"/app/email/confirm/{reset}").status_code == 400
    forged = URLSafeTimedSerializer("wrong-key", salt="orderflow:change-email").dumps(
        {"uid": owner.id, "v": owner.auth_version, "e": owner.email, "new": "evil@example.com"}
    )
    assert browser_for(app, owner).get(f"/app/email/confirm/{forged}").status_code == 400


def test_an_address_taken_after_the_link_was_sent_is_refused(app, company, owner, mail):
    browser = browser_for(app, owner)
    ask_for_new_email(browser, "priya@newdomain.example")
    link = link_from(mail[-1], "/app/email/confirm/")
    factories.make_client(company, email="priya@newdomain.example")
    response = browser.get(link)
    assert response.status_code == 409
    assert "priya@newdomain.example is now used by another OrderFlow account" in text(response)
    assert get_user(owner.id).email == "owner@kaveri.example"


def test_old_reset_links_stop_working_after_the_change(app, owner, mail):
    browser = browser_for(app, owner)
    reset = make_token(RESET_PASSWORD, owner)
    ask_for_new_email(browser, "priya@newdomain.example")
    browser.get(link_from(mail[-1], "/app/email/confirm/"))
    assert app.test_client().get(f"/reset-password/{reset}").status_code == 400


def test_a_name_with_a_link_must_be_fixed_before_asking(app, owner, mail):
    owner.first_name = "Visit evil.com"
    db.session.commit()
    response = ask_for_new_email(browser_for(app, owner), "new@newdomain.example")
    assert response.status_code == 200 and "Your first name includes a web address" in text(response)
    assert mail == []


def test_mail_failure_is_reported(app, owner, mail):
    browser = browser_for(app, owner)
    app.config["MAIL_BACKEND"] = "not-a-backend"
    response = ask_for_new_email(browser, "new@newdomain.example")
    app.config["MAIL_BACKEND"] = "memory"
    assert response.status_code == 200
    assert "We couldn&#39;t send the email to new@newdomain.example just now." in text(response)


def test_clients_cannot_use_the_staff_form(app, buyer, mail):
    response = ask_for_new_email(browser_for(app, buyer), "new@sunrise.example")
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert mail == [] and get_user(buyer.id).email == "buyer@sunrise.example"


# Clients who have joined ------------------------------------------------------------------


EDIT = {"first_name": "Ananya", "last_name": "Test", "buyer_company": "Buyer Pvt Ltd"}


def request_client_email(browser, client_user, new_email, **changes):
    return browser.post(
        f"/app/clients/{client_user.id}/edit", data={**EDIT, **changes, "email": new_email}, follow_redirects=True
    )


def test_staff_can_ask_to_change_a_joined_clients_email(app, owner, buyer, mail):
    staff = browser_for(app, owner)
    response = request_client_email(staff, buyer, "Ananya@NewShop.example", phone="+91 98450 12345")
    body = text(response)
    assert "Saved changes to Ananya Test (Buyer Pvt Ltd). We sent a link to ananya@newshop.example" in body
    assert "keep using buyer@sunrise.example" in body
    user = get_user(buyer.id)
    assert user.email == "buyer@sunrise.example" and user.phone == "+91 98450 12345"

    [message] = mail
    assert message.to == ["ananya@newshop.example"]
    html = check_layout(message)
    assert "Kaveri Supplies asked to change the email you use to sign in to OrderFlow to this address" in html.text

    # Staff can't confirm it for them
    link = link_from(message, "/app/email/confirm/")
    assert staff.get(link).status_code == 403
    assert get_user(buyer.id).email == "buyer@sunrise.example"

    # The client confirms it by signing in with their current details
    client_browser = browser_for(app, buyer)
    response = client_browser.get(link)
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert "Your sign-in email is now ananya@newshop.example." in text(client_browser.get("/portal/"))
    assert get_user(buyer.id).email == "ananya@newshop.example"
    assert can_sign_in(app, "ananya@newshop.example")

    notice = mail[-1]
    assert notice.to == ["buyer@sunrise.example"] and "contact Kaveri Supplies straight away" in notice.text


def test_only_the_email_changing_saves_nothing_else(app, owner, buyer, mail):
    response = request_client_email(browser_for(app, owner), buyer, "ananya@newshop.example")
    assert "Saved changes" not in text(response) and "We sent a link to ananya@newshop.example" in text(response)


def test_a_suspended_client_must_be_reactivated_first(app, owner, buyer, mail):
    buyer.active = False
    db.session.commit()
    response = request_client_email(browser_for(app, owner), buyer, "ananya@newshop.example")
    assert "is suspended, so they can&#39;t confirm a new email address. Reactivate them first." in text(response)
    assert mail == []


def test_a_taken_address_is_refused_for_a_client(app, owner, admin, buyer, mail):
    response = request_client_email(browser_for(app, owner), buyer, "OPS@kaveri.example")
    assert "belongs to someone on your team" in text(response)
    assert mail == []


def test_mail_failure_for_a_client_is_reported(app, owner, buyer):
    staff = browser_for(app, owner)
    app.config["MAIL_BACKEND"] = "not-a-backend"
    response = request_client_email(staff, buyer, "ananya@newshop.example", last_name="Rao")
    app.config["MAIL_BACKEND"] = "memory"
    body = text(response)
    assert "Saved changes to Ananya Rao (Buyer Pvt Ltd). We couldn&#39;t email the confirmation link" in body
    assert get_user(buyer.id).email == "buyer@sunrise.example" and get_user(buyer.id).last_name == "Rao"


def test_another_companys_staff_cannot_touch_the_client(app, buyer, other_company, mail):
    rival = browser_for(app, other_company["owner"])
    response = rival.post(f"/app/clients/{buyer.id}/edit", data={**EDIT, "email": "evil@rival.example"})
    assert response.status_code == 404 and mail == []


# Limits -----------------------------------------------------------------------------------


@pytest.fixture
def limited_app():
    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_asking_is_rate_limited_per_person(limited_app):
    owner = factories.make_owner(factories.make_company("Kaveri"), email="o@kaveri.example")
    browser = browser_for(limited_app, owner)
    statuses = [ask_for_new_email(browser, "x@kaveri.example", password="wrong").status_code for _ in range(6)]
    assert statuses == [200] * 5 + [429]


def test_confirmation_links_count_against_the_daily_allowance(limited_app, monkeypatch):
    from limits import parse

    monkeypatch.setattr(accounts, "_DAILY_INVITATIONS", parse("1 per day"))
    company = factories.make_company("Kaveri")
    owner = factories.make_owner(company, email="o@kaveri.example")
    buyer = factories.make_client(company, email="b@shop.example", first_name="Ananya")
    browser = browser_for(limited_app, owner)
    assert ask_for_new_email(browser, "o2@kaveri.example").status_code == 302
    response = request_client_email(browser, buyer, "b2@shop.example")
    assert "Your workspace has sent its 1 invitation emails for today." in text(response)
    assert get_user(buyer.id).email == "b@shop.example"
