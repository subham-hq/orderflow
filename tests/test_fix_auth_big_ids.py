"""Record ids too big for the database are a 404, not a server error.

Postgres raises "integer out of range" for an id above 2,147,483,647 and
SQLite overflows above 2**63 - 1, so the shared loader checks the range
before querying.
"""

import pytest

from orderflow.extensions import db
from orderflow.models import Bulletin
from tests import factories

TOO_BIG = [2_147_483_648, 99_999_999_999, 99_999_999_999_999_999_999_999]

PAGES = [
    "/app/products/{id}/edit",
    "/app/clients/{id}",
    "/app/clients/{id}/edit",
    "/app/bulletins/{id}/edit",
]

ACTIONS = [
    ("/app/products/{id}/edit", {"name": "x"}),
    ("/app/products/{id}/status", {"active": "0"}),
    ("/app/clients/{id}/edit", {"first_name": "x"}),
    ("/app/clients/{id}/status", {"active": "0"}),
    ("/app/clients/{id}/resend-invite", {}),
    ("/app/clients/{id}/delete", {}),
    ("/app/bulletins/{id}/edit", {"title": "x"}),
    ("/app/bulletins/{id}/status", {"published": "0"}),
    ("/app/bulletins/{id}/delete", {}),
    ("/app/team/{id}/status", {"active": "0"}),
    ("/app/team/{id}/resend-invite", {}),
    ("/app/team/{id}/delete", {}),
]


@pytest.mark.parametrize("record_id", TOO_BIG)
@pytest.mark.parametrize("path", PAGES)
def test_pages_with_an_id_too_big_for_the_database_are_not_found(client, login, owner, path, record_id):
    login(owner)
    assert client.get(path.format(id=record_id)).status_code == 404


@pytest.mark.parametrize("record_id", TOO_BIG)
@pytest.mark.parametrize(("path", "data"), ACTIONS)
def test_actions_with_an_id_too_big_for_the_database_are_not_found(client, login, owner, path, data, record_id):
    login(owner)
    assert client.post(path.format(id=record_id), data=data).status_code == 404


def test_the_largest_valid_id_and_real_records_still_work(client, login, company, owner):
    product = factories.make_product(company)
    bulletin = Bulletin(company=company, author=owner, title="Holiday hours", body="Closed on Monday.")
    db.session.add(bulletin)
    db.session.commit()
    login(owner)
    assert client.get("/app/products/2147483647/edit").status_code == 404
    assert client.get(f"/app/products/{product.id}/edit").status_code == 200
    assert client.get(f"/app/bulletins/{bulletin.id}/edit").status_code == 200
