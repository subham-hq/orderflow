"""Order numbers and status changes stay consistent when requests overlap."""

import os
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.orm.attributes import set_committed_value

from orderflow import create_app
from orderflow.extensions import db
from orderflow.models import APPROVED, PENDING, REJECTED, Company, Order, User
from orderflow.services.orders import OrderError, place_order, transition_order
from tests import factories


@pytest.fixture
def product(company):
    return factories.make_product(company, sku="CTN-001", price_paise=4_250)


def test_order_numbers_ignore_a_stale_company_in_the_session(company, buyer, product):
    assert buyer.company.next_order_seq == 1  # the Company is now in the session
    db.session.execute(text("UPDATE companies SET next_order_seq = 7 WHERE id = :id"), {"id": company.id})
    order, _ = place_order(buyer, {product.id: 1})
    assert order.number == "ORD-000007"
    assert db.session.get(Company, company.id).next_order_seq == 8


def test_numbers_already_taken_are_skipped(company, buyer, product):
    first, _ = place_order(buyer, {product.id: 1})
    second, _ = place_order(buyer, {product.id: 1})
    # The counter falls behind, say after orders were added by hand
    db.session.execute(text("UPDATE companies SET next_order_seq = 1 WHERE id = :id"), {"id": company.id})
    db.session.commit()
    third, _ = place_order(buyer, {product.id: 1})
    assert [first.number, second.number, third.number] == ["ORD-000001", "ORD-000002", "ORD-000003"]


def test_a_status_change_based_on_an_old_read_is_refused(company, owner, buyer, product, mail):
    order, _ = place_order(buyer, {product.id: 1})
    # Someone else approves the order...
    db.session.execute(db.update(Order).where(Order.id == order.id).values(status=APPROVED))
    db.session.commit()
    # ...after this request read it as pending
    set_committed_value(order, "status", PENDING)
    sent = len(mail)

    with pytest.raises(OrderError, match="Someone else changed this order"):
        transition_order(order, owner, REJECTED, "Out of stock")

    db.session.refresh(order)
    assert order.status == APPROVED and order.rejection_reason is None
    assert [event.to_status for event in order.events] == [PENDING]
    assert len(mail) == sent  # the client isn't told it was rejected


# Real concurrency, on a database that several connections share -------------


@pytest.fixture
def shared_app(tmp_path):
    """An app on a database file (or the Postgres test database) that threads can share."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'race.db'}"
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=url)
    with app.app_context():
        db.create_all()
        company = factories.make_company("Kaveri Supplies")
        factories.make_owner(company, email="owner@kaveri.example", password=None)
        clients = [factories.make_client(company, email=f"buyer{n}@example.com", password=None).id for n in range(8)]
        product = factories.make_product(company, sku="CTN-001").id
    yield app, clients, product
    with app.app_context():
        db.session.remove()
        db.drop_all()


def run_together(app, jobs):
    """Run each job in its own thread and app context, starting them at the same moment."""
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def work(index, job):
        with app.app_context():
            try:
                prepared = job[0]()
                barrier.wait()
                results[index] = job[1](prepared)
            except Exception as error:  # reported by the test below
                results[index] = error
            finally:
                db.session.remove()

    threads = [threading.Thread(target=work, args=(index, job)) for index, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_checkouts_at_the_same_moment_get_different_numbers(shared_app):
    app, clients, product = shared_app
    jobs = [
        (lambda client_id=client_id: db.session.get(User, client_id), lambda user: place_order(user, {product: 1}))
        for client_id in clients
    ]
    results = run_together(app, jobs)

    errors = [result for result in results if isinstance(result, Exception)]
    assert not errors, errors
    numbers = sorted(order.number for order, created in results)
    assert numbers == [f"ORD-{n:06d}" for n in range(1, len(clients) + 1)]
    with app.app_context():
        assert db.session.scalar(db.select(Company.next_order_seq)) == len(clients) + 1


def test_approving_and_rejecting_at_the_same_moment_only_one_wins(shared_app):
    app, clients, product = shared_app
    with app.app_context():
        order, _ = place_order(db.session.get(User, clients[0]), {product: 1})
        order_id = order.id
        owner_id = db.session.scalar(db.select(User.id).where(User.email == "owner@kaveri.example"))

    def load():
        return db.session.get(Order, order_id), db.session.get(User, owner_id)

    jobs = [
        (load, lambda loaded: transition_order(*loaded, APPROVED)),
        (load, lambda loaded: transition_order(*loaded, REJECTED, "Out of stock")),
    ]
    results = run_together(app, jobs)

    refused = [result for result in results if isinstance(result, OrderError)]
    assert len(refused) == 1, results
    assert not [result for result in results if isinstance(result, Exception) and result not in refused]
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert [event.from_status for event in order.events] == [None, PENDING]
        assert order.status in (APPROVED, REJECTED)
