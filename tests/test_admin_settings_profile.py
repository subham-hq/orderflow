import pytest

from orderflow.extensions import db
from orderflow.models import User


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def text(response):
    return response.get_data(as_text=True)


@pytest.mark.parametrize("method", ["get", "post"])
def test_anonymous_users_are_sent_to_sign_in(client, owner, method):
    response = getattr(client, method)("/app/settings/profile", data={"first_name": "Hijacked"})
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert db.session.get(User, owner.id).first_name == "Priya"


@pytest.mark.parametrize("method", ["get", "post"])
def test_clients_are_sent_back_to_their_portal(client, login, buyer, method):
    login(buyer)
    response = getattr(client, method)("/app/settings/profile", data={"first_name": "Hijacked"})
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert db.session.get(User, buyer.id).first_name == "Ananya"


# Profile ---------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_staff_can_edit_their_own_profile(client, login, owner, admin, who):
    user = owner if who == "owner" else admin
    other = admin if who == "owner" else owner
    login(user)
    body = text(client.get("/app/settings/profile"))
    assert f'value="{user.first_name}"' in body and user.email in body
    assert 'name="email"' not in body and "/account/password" in body
    assert 'aria-current="page">Your profile</a>' in body and "None" not in body
    data = {"first_name": " Meera ", "last_name": "Iyer", "phone": "98450 12345", "email": "new@evil.example"}
    response = client.post("/app/settings/profile", data=data, follow_redirects=True)
    assert "Saved your profile." in text(response)
    user = db.session.get(User, user.id)
    assert (user.first_name, user.last_name, user.phone, user.email) == ("Meera", "Iyer", "98450 12345", user.email)
    assert user.email != "new@evil.example"
    assert db.session.get(User, other.id).first_name != "Meera"


def test_profile_without_changes_says_so(client, signed_in):
    data = {"first_name": "Priya", "last_name": "Test", "phone": ""}
    response = client.post("/app/settings/profile", data=data, follow_redirects=True)
    assert "No changes to save." in text(response)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"first_name": ""}, "Enter your first name."),
        ({"last_name": "x" * 81}, "Keep the name to 80 characters or fewer."),
        ({"phone": "+91 (80) abc"}, "Enter a phone number"),
    ],
)
def test_profile_validation_keeps_input(client, signed_in, changes, message):
    data = {"first_name": "Typed", "last_name": "Name", "phone": "", **changes}
    response = client.post("/app/settings/profile", data=data)
    body = text(response)
    assert response.status_code == 200 and message in body
    assert db.session.get(User, signed_in.id).first_name == "Priya"


def test_profile_last_name_and_phone_can_be_cleared(client, signed_in):
    client.post("/app/settings/profile", data={"first_name": "Priya", "last_name": "", "phone": ""})
    user = db.session.get(User, signed_in.id)
    assert user.last_name is None and user.phone is None
