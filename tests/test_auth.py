from orderflow.extensions import db
from orderflow.models import OWNER, Company, User
from orderflow.services.accounts import invite_user
from tests import factories
from tests.conftest import link_from

SIGNUP = {
    "first_name": "Meera",
    "last_name": "Iyer",
    "company_name": "Iyer Traders",
    "email": "meera@iyertraders.example",
    "password": "correct horse battery",
    "accept_terms": "y",
}


def test_signup_creates_company_and_unverified_owner(client, mail):
    response = client.post("/signup", data=SIGNUP)
    assert response.headers["Location"].endswith("/verify")

    user = db.session.scalar(db.select(User).where(User.email == SIGNUP["email"]))
    assert user.role == OWNER
    assert user.company.name == "Iyer Traders"
    assert not user.is_verified
    assert len(mail) == 1 and mail[0].to == [SIGNUP["email"]]


def test_unverified_user_is_held_at_verify_page_until_link_is_opened(client, mail):
    client.post("/signup", data=SIGNUP)
    assert client.get("/app/").headers["Location"].endswith("/verify")

    response = client.get(link_from(mail[0], "/verify/"))
    assert response.status_code == 302
    assert client.get("/app/").status_code == 200


def test_signup_normalises_email_and_rejects_duplicates_case_insensitively(client):
    client.post("/signup", data={**SIGNUP, "email": "  Meera@IyerTraders.Example "})
    user = db.session.scalar(db.select(User))
    assert user.email == "meera@iyertraders.example"

    client.post("/logout")
    response = client.post("/signup", data={**SIGNUP, "email": "MEERA@iyertraders.example", "company_name": "Other"})
    assert response.status_code == 200
    assert "already exists" in response.get_data(as_text=True)
    assert db.session.scalar(db.select(db.func.count(Company.id))) == 1


def test_signup_rejects_weak_passwords(client):
    for password in ["short", "password123", "aaaaaaaaaaaa", "meera12345"]:
        response = client.post("/signup", data={**SIGNUP, "password": password})
        assert response.status_code == 200
        assert db.session.scalar(db.select(db.func.count(User.id))) == 0


def test_two_companies_can_share_a_name(client):
    # v1 used company names as a global namespace; anyone could squat a name
    client.post("/signup", data=SIGNUP)
    client.post("/logout")
    client.post("/signup", data={**SIGNUP, "email": "someone@else.example"})
    assert db.session.scalar(db.select(db.func.count(Company.id))) == 2


def test_login_is_case_insensitive_and_lands_on_the_right_home(client, owner, buyer):
    response = client.post("/login", data={"email": "OWNER@Kaveri.example", "password": factories.PASSWORD})
    assert response.headers["Location"].endswith("/app/")
    client.post("/logout")
    response = client.post("/login", data={"email": buyer.email, "password": factories.PASSWORD})
    assert response.headers["Location"].endswith("/portal/")


def test_login_failure_message_is_the_same_for_unknown_email_and_wrong_password(client, owner):
    unknown = client.post("/login", data={"email": "nobody@nowhere.example", "password": "whatever12345"})
    wrong = client.post("/login", data={"email": owner.email, "password": "not the password"})
    message = "That email and password don&#39;t match"
    assert message in unknown.get_data(as_text=True)
    assert message in wrong.get_data(as_text=True)


def test_suspended_user_cannot_sign_in(client, buyer):
    buyer.active = False
    db.session.commit()
    response = client.post("/login", data={"email": buyer.email, "password": factories.PASSWORD})
    assert response.status_code == 200
    assert "suspended" in response.get_data(as_text=True)


def test_suspending_a_signed_in_user_ends_their_session(client, login, buyer):
    login(buyer)
    assert client.get("/portal/").status_code == 200
    buyer.active = False
    buyer.end_all_sessions()
    db.session.commit()
    assert client.get("/portal/").headers["Location"].startswith("/login")


def test_invited_user_without_password_cannot_sign_in(client, company, owner):
    pending = factories.make_client(company, email="pending@example.com", password=None)
    response = client.post("/login", data={"email": pending.email, "password": ""})
    assert response.status_code == 200
    assert client.get("/portal/").status_code == 302


def test_logout_requires_post(client, login, owner):
    login(owner)
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert client.get("/app/").status_code == 302


def test_next_parameter_cannot_redirect_off_site(client, owner):
    for target in ["https://evil.example/", "//evil.example", "/\\evil.example"]:
        response = client.post(f"/login?next={target}", data={"email": owner.email, "password": factories.PASSWORD})
        assert response.headers["Location"] == "/app/"
        client.post("/logout")
    response = client.post("/login?next=/app/orders", data={"email": owner.email, "password": factories.PASSWORD})
    assert response.headers["Location"] == "/app/orders"


def test_forgot_password_does_not_reveal_whether_an_account_exists(client, owner, mail):
    known = client.post("/forgot-password", data={"email": owner.email}).get_data(as_text=True)
    unknown = client.post("/forgot-password", data={"email": "nobody@nowhere.example"}).get_data(as_text=True)
    assert known.replace(owner.email, "X") == unknown.replace("nobody@nowhere.example", "X")
    assert len(mail) == 1


def test_password_reset_link_works_once_and_signs_out_other_sessions(app, client, login, owner, mail):
    other_device = app.test_client()
    other_device.post("/login", data={"email": owner.email, "password": factories.PASSWORD})
    assert other_device.get("/app/").status_code == 200

    client.post("/forgot-password", data={"email": owner.email})
    link = link_from(mail[-1], "/reset-password/")
    response = client.post(link, data={"password": "a brand new password", "confirm": "a brand new password"})
    assert response.headers["Location"].endswith("/app/")

    assert other_device.get("/app/").status_code == 302
    assert (
        client.post(link, data={"password": "yet another password", "confirm": "yet another password"}).status_code
        == 400
    )
    client.post("/logout")
    login(owner, password="a brand new password")


def test_reset_link_for_tampered_token_is_rejected(client):
    assert client.get("/reset-password/not-a-real-token").status_code == 400


def test_invite_acceptance_sets_password_verifies_and_is_single_use(client, company, owner, mail):
    with client.application.test_request_context():
        invited = invite_user(company, owner, "New.Buyer@Example.com", "client", "Ravi", buyer_company="Ravi Stores")
    assert invited.email == "new.buyer@example.com"
    link = link_from(mail[-1], "/invite/")

    page = client.get(link)
    assert page.status_code == 200 and "Ravi" in page.get_data(as_text=True)
    response = client.post(link, data={"password": "ravi stores 2026", "confirm": "ravi stores 2026"})
    assert response.headers["Location"].endswith("/portal/")
    db.session.refresh(invited)
    assert invited.is_verified and invited.has_accepted_invite

    client.post("/logout")
    assert client.get(link).status_code == 400


def test_change_password_requires_current_password_and_ends_other_sessions(app, client, login, owner):
    login(owner)
    other = app.test_client()
    other.post("/login", data={"email": owner.email, "password": factories.PASSWORD})

    wrong = client.post(
        "/account/password",
        data={"current_password": "nope", "password": "a different password", "confirm": "a different password"},
    )
    assert "isn&#39;t your current password" in wrong.get_data(as_text=True)

    response = client.post(
        "/account/password",
        data={
            "current_password": factories.PASSWORD,
            "password": "a different password",
            "confirm": "a different password",
        },
    )
    assert response.status_code == 302
    assert client.get("/app/").status_code == 200  # this device stays signed in
    assert other.get("/app/").status_code == 302  # the other one is signed out


def test_resend_verification(client, mail):
    client.post("/signup", data=SIGNUP)
    client.post("/verify/resend")
    assert len(mail) == 2


def test_changing_password_keeps_the_clients_cart(client, login, company, buyer):
    product = factories.make_product(company)
    login(buyer)
    with client.session_transaction() as session:
        session["cart"] = {str(product.id): 3}
    client.post(
        "/account/password",
        data={
            "current_password": factories.PASSWORD,
            "password": "a different password",
            "confirm": "a different password",
        },
    )
    with client.session_transaction() as session:
        assert session["cart"] == {str(product.id): 3}


def test_signing_in_never_inherits_a_previous_cart(client, login, company, buyer, owner):
    login(buyer)
    with client.session_transaction() as session:
        session["cart"] = {"1": 5}
    client.post("/login", data={"email": owner.email, "password": factories.PASSWORD})
    client.post("/logout")
    login(owner)
    with client.session_transaction() as session:
        assert "cart" not in session
