from datetime import UTC, datetime, timedelta

import pytest

from orderflow import timeutil
from orderflow.extensions import db
from orderflow.models import Bulletin
from orderflow.timeutil import as_utc
from tests import factories

# 08:30 in India on 18 Oct 2026
NOW = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)

NEW = {"title": "Diwali dispatch schedule", "body": "Orders approved by 25 Oct ship first.", "publish": "y"}


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(timeutil, "utcnow", lambda: NOW)


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def make_bulletin(company, title="Notice", body="Hello", published=True, expires_at=None, created_at=None, **fields):
    bulletin = Bulletin(
        company_id=company.id,
        title=title,
        body=body,
        is_published=published,
        expires_at=expires_at,
        created_at=created_at or NOW,
        **fields,
    )
    db.session.add(bulletin)
    db.session.commit()
    return bulletin


def bulletins():
    return db.session.scalars(db.select(Bulletin).order_by(Bulletin.id)).all()


def text(response):
    return response.get_data(as_text=True)


# Access ----------------------------------------------------------------------

ENDPOINTS = [
    ("get", "/app/bulletins"),
    ("get", "/app/bulletins/new"),
    ("post", "/app/bulletins/new"),
    ("get", "/app/bulletins/{id}/edit"),
    ("post", "/app/bulletins/{id}/edit"),
    ("post", "/app/bulletins/{id}/status"),
    ("post", "/app/bulletins/{id}/delete"),
]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, company, method, path):
    bulletin = make_bulletin(company)
    response = getattr(client, method)(path.format(id=bulletin.id), data={**NEW, "published": "0"})
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert [(b.title, b.is_published) for b in bulletins()] == [("Notice", True)]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, company, buyer, method, path):
    bulletin = make_bulletin(company)
    login(buyer)
    response = getattr(client, method)(path.format(id=bulletin.id), data={**NEW, "published": "0"})
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert [(b.title, b.is_published) for b in bulletins()] == [("Notice", True)]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/app/bulletins/{id}/edit"),
        ("post", "/app/bulletins/{id}/edit"),
        ("post", "/app/bulletins/{id}/status"),
        ("post", "/app/bulletins/{id}/delete"),
    ],
)
def test_bulletins_of_other_companies_are_not_found(client, signed_in, other_company, method, path):
    rival = make_bulletin(other_company["company"], title="Rival notice")
    response = getattr(client, method)(path.format(id=rival.id), data={**NEW, "published": "0"})
    assert response.status_code == 404
    rival = db.session.get(Bulletin, rival.id)
    assert rival is not None and rival.title == "Rival notice" and rival.is_published


def test_unknown_ids_and_get_requests(client, signed_in, company):
    bulletin = make_bulletin(company)
    assert client.get("/app/bulletins/999999/edit").status_code == 404
    assert client.get(f"/app/bulletins/{bulletin.id}/status").status_code == 405
    assert client.get(f"/app/bulletins/{bulletin.id}/delete").status_code == 405


def test_admins_can_manage_bulletins(client, login, company, admin):
    login(admin)
    assert client.post("/app/bulletins/new", data=NEW).status_code == 302
    [bulletin] = bulletins()
    assert bulletin.author_id == admin.id and bulletin.company_id == company.id


# List ------------------------------------------------------------------------


def test_empty_list_explains_what_to_do(client, signed_in, other_company):
    make_bulletin(other_company["company"], title="Rival notice")
    body = text(client.get("/app/bulletins"))
    assert "Post your first bulletin" in body and "/app/bulletins/new" in body
    assert "Rival notice" not in body and 'aria-current="page"' in body


def test_every_bulletin_is_listed_newest_first_with_its_status(client, signed_in, company, owner):
    make_bulletin(company, title="Old expired", expires_at=NOW - timedelta(hours=1), created_at=NOW - timedelta(days=9))
    make_bulletin(company, title="Draft notice", published=False, created_at=NOW - timedelta(days=5))
    make_bulletin(company, title="Live forever", created_at=NOW - timedelta(days=2), author_id=owner.id)
    make_bulletin(company, title="Live for now", expires_at=NOW + timedelta(minutes=1), created_at=NOW)
    body = text(client.get("/app/bulletins"))
    assert (
        body.index("Live for now") < body.index("Live forever") < body.index("Draft notice") < body.index("Old expired")
    )
    assert body.count(">Live</span>") == 2 and ">Draft</span>" in body and ">Expired</span>" in body
    assert "Posted by Priya Test on 16 Oct 2026" in body and "No end date" in body
    assert "Showing 1–4 of 4 bulletins." in body and "None" not in body


def test_expiry_is_shown_in_the_company_time_zone(client, signed_in, company):
    make_bulletin(company, title="Closing early", expires_at=datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
    body = text(client.get("/app/bulletins"))
    assert "Ends 18 Oct 2026, 2:30 PM" in body and "Times are in Asia/Kolkata." in body
    company.timezone = "America/New_York"
    db.session.commit()
    body = text(client.get("/app/bulletins"))
    assert "Ends 18 Oct 2026, 5:00 AM" in body and "Times are in America/New_York." in body


def test_line_breaks_are_kept_and_html_is_escaped(client, signed_in, company):
    make_bulletin(company, title="<b>Bold</b>", body="Line one\nLine two\n\nNew paragraph <script>x</script>")
    body = text(client.get("/app/bulletins"))
    assert "<p>Line one<br>Line two</p>" in body
    assert "<p>New paragraph &lt;script&gt;x&lt;/script&gt;</p>" in body
    assert "&lt;b&gt;Bold&lt;/b&gt;" in body and "<b>Bold</b>" not in body


def test_status_buttons(client, signed_in, company):
    live = make_bulletin(company, title="Live")
    draft = make_bulletin(company, title="Draft", published=False)
    ended_draft = make_bulletin(company, title="Ended draft", published=False, expires_at=NOW - timedelta(days=1))
    body = text(client.get("/app/bulletins"))
    assert f'action="/app/bulletins/{live.id}/status"' in body and 'name="published" value="0"' in body
    assert f'action="/app/bulletins/{draft.id}/status"' in body and 'name="published" value="1"' in body
    assert f'action="/app/bulletins/{ended_draft.id}/status"' not in body
    assert "Its end time has passed" in body and "Ended 17 Oct 2026" in body
    assert f'action="/app/bulletins/{live.id}/delete"' in body and "data-confirm=" in body


def test_list_paginates(client, signed_in, company):
    for n in range(27):
        make_bulletin(company, title=f"Notice {n:02d}", created_at=NOW - timedelta(hours=n))
    first = text(client.get("/app/bulletins"))
    assert "Notice 00" in first and "Notice 24" in first and "Notice 25" not in first
    second = text(client.get("/app/bulletins?page=2"))
    assert "Notice 26" in second and "Notice 00" not in second


# New -------------------------------------------------------------------------


def test_new_form(client, signed_in):
    body = text(client.get("/app/bulletins/new"))
    assert 'type="datetime-local"' in body and "In Asia/Kolkata time." in body
    assert 'name="publish" type="checkbox" value="y"' in body and "checked" in body


def test_publishing_a_bulletin_without_an_end(client, signed_in, company):
    response = client.post("/app/bulletins/new", data={**NEW, "title": "  Diwali   dispatch schedule "})
    assert response.headers["Location"] == "/app/bulletins"
    [bulletin] = bulletins()
    assert (bulletin.title, bulletin.is_published, bulletin.expires_at) == ("Diwali dispatch schedule", True, None)
    assert bulletin.author_id == signed_in.id and bulletin.company_id == company.id
    body = text(client.get("/app/bulletins"))
    assert "Published “Diwali dispatch schedule”. Clients see it on their dashboard until you take it down." in body


def test_expiry_is_read_in_the_company_time_zone(client, signed_in):
    client.post("/app/bulletins/new", data={**NEW, "expires_at": "2026-10-18T14:30"})
    [bulletin] = bulletins()
    assert as_utc(bulletin.expires_at) == datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    flash = text(client.get("/app/bulletins"))
    assert "until 18 Oct 2026, 2:30 PM" in flash and ">Live</span>" in flash


def test_expiry_follows_a_different_company_time_zone(client, signed_in, company):
    company.timezone = "America/New_York"
    db.session.commit()
    client.post("/app/bulletins/new", data={**NEW, "expires_at": "2026-10-18T14:30"})
    assert as_utc(bulletins()[0].expires_at) == datetime(2026, 10, 18, 18, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expires_at", "message"),
    [
        ("2026-10-18T08:29", "That time has already passed."),  # 02:59 UTC, a minute ago
        ("2026-10-18T08:30", "That time has already passed."),  # exactly now
        ("2020-01-01T00:00", "That time has already passed."),
        ("tomorrow", "Enter a date and time"),
        ("2026-13-01T10:00", "Enter a date and time"),
        ("0001-01-01T00:00", "Enter a date and time"),
        ("2040-01-01T00:00", "Choose a time within the next 5 years"),
    ],
)
def test_bad_expiry_times_are_rejected_and_input_kept(client, signed_in, expires_at, message):
    response = client.post("/app/bulletins/new", data={**NEW, "expires_at": expires_at})
    body = text(response)
    assert response.status_code == 200 and message in body
    assert f'value="{expires_at}"' in body and "Diwali dispatch schedule" in body
    assert "Orders approved by 25 Oct ship first." in body
    assert bulletins() == []


def test_a_draft_may_keep_a_past_end_time(client, signed_in):
    data = {"title": "Old note", "body": "For the record", "expires_at": "2026-10-01T10:00"}
    response = client.post("/app/bulletins/new", data=data)
    assert response.status_code == 302
    [bulletin] = bulletins()
    assert bulletin.is_published is False
    assert "Saved “Old note”. It&#39;s a draft" in text(client.get("/app/bulletins"))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"title": " "}, "Give the bulletin a title."),
        ({"body": "\n\n"}, "Write the message your clients will see."),
        ({"title": "t" * 161}, "Keep the title to 160 characters or fewer."),
        ({"body": "b" * 4001}, "Keep the message to 4,000 characters or fewer."),
    ],
)
def test_validation_errors_keep_input(client, signed_in, changes, message):
    response = client.post("/app/bulletins/new", data={**NEW, "expires_at": "2026-10-20T10:00", **changes})
    body = text(response)
    assert response.status_code == 200 and message in body and 'value="2026-10-20T10:00"' in body
    assert bulletins() == []


def test_body_keeps_line_breaks(client, signed_in):
    client.post("/app/bulletins/new", data={**NEW, "body": "  First line\r\nSecond line  "})
    assert bulletins()[0].body == "First line\nSecond line"


def test_longest_allowed_values(client, signed_in):
    assert client.post("/app/bulletins/new", data={**NEW, "title": "t" * 160, "body": "b" * 4000}).status_code == 302


# Edit ------------------------------------------------------------------------


def test_edit_form_is_prefilled_in_company_time(client, signed_in, company):
    bulletin = make_bulletin(
        company, title="Closing", body="Line 1\nLine 2", expires_at=datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    )
    body = text(client.get(f"/app/bulletins/{bulletin.id}/edit"))
    assert 'value="2026-10-18T14:30"' in body and 'value="Closing"' in body
    assert "Line 1\nLine 2</textarea>" in body and ">Live</span>" in body


def test_saving_unchanged_keeps_the_exact_end_time(client, signed_in, company):
    exact = datetime(2026, 10, 25, 9, 0, 45, 123456, tzinfo=UTC)
    bulletin = make_bulletin(company, title="Closing", body="Soon", expires_at=exact)
    for _ in range(2):
        data = {"title": "Closing", "body": "Soon", "publish": "y", "expires_at": "2026-10-25T14:30"}
        response = client.post(f"/app/bulletins/{bulletin.id}/edit", data=data, follow_redirects=True)
        assert "No changes to save for “Closing”." in text(response)
    assert as_utc(db.session.get(Bulletin, bulletin.id).expires_at) == exact


def test_unchanged_end_a_few_seconds_away_still_saves(client, signed_in, company):
    soon = NOW + timedelta(seconds=30)  # shows as 08:30, which is "now" to the minute
    bulletin = make_bulletin(company, title="Closing", body="Soon", expires_at=soon)
    data = {"title": "Closing now", "body": "Soon", "publish": "y", "expires_at": "2026-10-18T08:30"}
    response = client.post(f"/app/bulletins/{bulletin.id}/edit", data=data)
    assert response.status_code == 302
    bulletin = db.session.get(Bulletin, bulletin.id)
    assert bulletin.title == "Closing now" and as_utc(bulletin.expires_at) == soon


def test_editing_changes_the_bulletin(client, signed_in, company):
    bulletin = make_bulletin(company, title="Old", body="Old body", expires_at=NOW + timedelta(days=3))
    data = {"title": "New", "body": "New body", "expires_at": ""}
    response = client.post(f"/app/bulletins/{bulletin.id}/edit", data=data, follow_redirects=True)
    assert "Saved “New”. It&#39;s a draft" in text(response)
    bulletin = db.session.get(Bulletin, bulletin.id)
    assert (bulletin.title, bulletin.body, bulletin.is_published, bulletin.expires_at) == (
        "New",
        "New body",
        False,
        None,
    )


def test_republishing_an_expired_bulletin_needs_a_new_end(client, signed_in, company):
    bulletin = make_bulletin(company, title="Sale", body="Ends soon", expires_at=NOW - timedelta(days=1))
    page = text(client.get(f"/app/bulletins/{bulletin.id}/edit"))
    assert ">Expired</span>" in page
    data = {"title": "Sale", "body": "Ends soon", "publish": "y", "expires_at": "2026-10-17T08:30"}
    assert "That time has already passed." in text(client.post(f"/app/bulletins/{bulletin.id}/edit", data=data))
    data["expires_at"] = "2026-10-30T18:00"
    client.post(f"/app/bulletins/{bulletin.id}/edit", data=data)
    bulletin = db.session.get(Bulletin, bulletin.id)
    assert bulletin.is_live(NOW) and as_utc(bulletin.expires_at) == datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def test_edit_validation_keeps_input(client, signed_in, company):
    bulletin = make_bulletin(company, title="Keep", body="Body")
    data = {"title": "", "body": "Typed body", "publish": "y", "expires_at": "garbage"}
    body = text(client.post(f"/app/bulletins/{bulletin.id}/edit", data=data))
    assert "Give the bulletin a title." in body and "Typed body" in body and 'value="garbage"' in body
    assert db.session.get(Bulletin, bulletin.id).title == "Keep"


# Publish, unpublish, delete --------------------------------------------------


def test_publish_and_unpublish_are_explicit_and_idempotent(client, signed_in, company):
    bulletin = make_bulletin(company, title="Notice", published=False)
    url = f"/app/bulletins/{bulletin.id}/status"
    response = client.post(url, data={"published": "1"}, follow_redirects=True)
    assert "Published “Notice”." in text(response) and db.session.get(Bulletin, bulletin.id).is_published
    response = client.post(url, data={"published": "1"}, follow_redirects=True)
    assert "“Notice” is already published." in text(response)
    response = client.post(url, data={"published": "0"}, follow_redirects=True)
    assert "Unpublished “Notice”." in text(response) and not db.session.get(Bulletin, bulletin.id).is_published
    response = client.post(url, data={"published": "0"}, follow_redirects=True)
    assert "“Notice” is already a draft." in text(response)
    assert db.session.get(Bulletin, bulletin.id).is_published is False


def test_an_ended_bulletin_cannot_be_republished_without_a_new_end(client, signed_in, company):
    bulletin = make_bulletin(
        company, title="Sale", published=False, expires_at=datetime(2026, 10, 17, 12, 30, tzinfo=UTC)
    )
    response = client.post(f"/app/bulletins/{bulletin.id}/status", data={"published": "1"}, follow_redirects=True)
    assert "“Sale” ended on 17 Oct 2026, 6:00 PM. Edit it to choose a new end time" in text(response)
    assert db.session.get(Bulletin, bulletin.id).is_published is False


def test_an_expired_bulletin_can_be_unpublished(client, signed_in, company):
    bulletin = make_bulletin(company, title="Sale", expires_at=NOW - timedelta(hours=2))
    client.post(f"/app/bulletins/{bulletin.id}/status", data={"published": "0"})
    assert db.session.get(Bulletin, bulletin.id).is_published is False


@pytest.mark.parametrize("data", [{}, {"published": ""}, {"published": "yes"}, {"active": "1"}])
def test_status_needs_an_explicit_target(client, signed_in, company, data):
    bulletin = make_bulletin(company)
    assert client.post(f"/app/bulletins/{bulletin.id}/status", data=data).status_code == 400
    assert db.session.get(Bulletin, bulletin.id).is_published


def test_status_returns_to_a_safe_next_page(client, signed_in, company):
    bulletin = make_bulletin(company)
    url = f"/app/bulletins/{bulletin.id}/status"
    assert client.post(url, data={"published": "0", "next": "/app/bulletins?page=2"}).headers["Location"] == (
        "/app/bulletins?page=2"
    )
    assert client.post(url, data={"published": "1", "next": "http://evil.example"}).headers["Location"] == (
        "/app/bulletins"
    )


def test_delete(client, signed_in, company):
    bulletin = make_bulletin(company, title="Old news")
    keep = make_bulletin(company, title="Keep me")
    response = client.post(f"/app/bulletins/{bulletin.id}/delete", follow_redirects=True)
    assert "Deleted “Old news”." in text(response)
    assert [b.id for b in bulletins()] == [keep.id]


def test_deleted_author_leaves_the_bulletin(client, signed_in, company):
    author = factories.make_admin(company, email="gone@kaveri.example")
    make_bulletin(company, title="Orphan", author_id=author.id)
    db.session.delete(author)
    db.session.commit()
    body = text(client.get("/app/bulletins"))
    assert "Orphan" in body and "Posted on 18 Oct 2026" in body
