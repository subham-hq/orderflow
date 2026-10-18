import html as html_lib
import re

import pytest
from sqlalchemy import event, func, select
from werkzeug.datastructures import MultiDict

from orderflow.admin import orders as orders_view
from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, ORDER_STATUSES, PENDING, REJECTED, OrderEvent
from orderflow.services.orders import OrderError
from tests import factories
from tests.test_admin_orders_support import ist, make_order


@pytest.fixture
def carton(company):
    return factories.make_product(
        company,
        sku="CTN-1",
        name="Carton, 5-ply",
        category="Packaging",
        unit="box",
        price_paise=4_250,
        gst_rate_bp=1800,
    )


@pytest.fixture
def make(company, owner, buyer, carton):
    """make(status) -> an order from the buyer for 100 cartons in that status."""

    def _make(status=PENDING, **kwargs):
        kwargs.setdefault("placed_at", ist(2026, 10, 31, 23, 30))
        return make_order(company, buyer, [(carton, 100)], status, actor=owner, **kwargs)

    return _make


def get(client, path):
    response = client.get(path)
    assert response.status_code == 200, (response.status_code, response.headers.get("Location"))
    return response.get_data(as_text=True)


def post_status(client, order, data):
    return client.post(f"/app/orders/{order.number}/status", data=data)


def flashes(page):
    return [html_lib.unescape(text) for text in re.findall(r'<p class="alert__text">(.*?)</p>', page, re.S)]


def follow(client, response):
    assert response.status_code == 302
    return get(client, response.headers["Location"])


def to_statuses(page):
    return sorted(re.findall(r'name="to_status" (?:type="hidden" )?value="(\w+)"', page))


def event_count(order):
    return db.session.scalar(select(func.count(OrderEvent.id)).where(OrderEvent.order_id == order.id))


def assert_clean(page):
    assert 'style="' not in page and "<style" not in page
    assert not re.search(r"<script(?![^>]*\bsrc=)", page)
    assert not re.search(r"\son[a-z]+=", page)
    assert "None" not in re.sub(r"<[^>]+>", " ", page)


# The page --------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_pending_order_shows_everything_needed_to_decide(client, login, request, buyer, make, who):
    buyer.phone = "+91 98450 12345"
    db.session.commit()
    order = make(PENDING, notes="Deliver to gate 2.\nCall before coming.")
    login(request.getfixturevalue(who))
    page = get(client, f"/app/orders/{order.number}")

    assert f'<h1 class="page-header__title">{order.number}</h1>' in page
    assert "badge--pending" in page and "Pending review" in page
    # 23:30 on 31 October in India (18:00 UTC)
    assert "Placed 31 Oct 2026, 11:30 PM by Buyer Pvt Ltd" in page
    # Client card
    assert '<dd class="kv__value">Buyer Pvt Ltd</dd>' in page
    assert f'<dd class="kv__value">{buyer.full_name}</dd>' in page
    assert '<a class="text-link" href="mailto:buyer@sunrise.example">buyer@sunrise.example</a>' in page
    assert '<a class="text-link" href="tel:+919845012345">+91 98450 12345</a>' in page
    # Lines and totals from the snapshot
    assert "Carton, 5-ply" in page and "CTN-1 · Packaging" in page
    assert "100 <span" in page and "box" in page
    assert "₹42.50" in page and "₹765.00" in page and "(18%)" in page
    assert "₹4,250.00" in page and "₹5,015.00" in page
    assert '<caption class="visually-hidden">Items on order ' in page
    # Notes keep their line breaks (shown with white-space: pre-line)
    assert '<p class="order-note">Deliver to gate 2.\nCall before coming.</p>' in page
    # History
    assert f"Order placed by {buyer.full_name}" in page
    assert 'datetime="2026-10-31T23:30+0530"' in page
    # Actions
    assert to_statuses(page) == [APPROVED, REJECTED]
    assert 'data-confirm="Approve ' + order.number + " for ₹5,015.00? We&#39;ll email Buyer Pvt Ltd" in page
    textarea = re.search(r"<textarea[^>]*>", page).group(0)
    assert 'name="message"' in textarea and "required" in textarea and 'maxlength="1000"' in textarea
    assert '<label class="field__label" for="message">Reason for rejecting</label>' in page
    assert 'data-confirm-variant="danger"' in page
    assert f'href="/app/orders/{order.number}/invoice"' in page
    assert 'href="/app/orders"' in page
    assert_clean(page)


def test_approved_order_offers_fulfil_and_cancel(client, login, owner, make):
    order = make(APPROVED)
    login(owner)
    page = get(client, f"/app/orders/{order.number}")
    assert to_statuses(page) == [CANCELLED, FULFILLED]
    assert "Approved by " + owner.full_name + " on 1 Nov 2026" in page
    assert "Note for the client (optional)" in page
    textarea = re.search(r"<textarea[^>]*>", page).group(0)
    assert "required" not in textarea
    assert f"data-confirm=\"Cancel {order.number}? This can't be undone. We'll email Buyer Pvt Ltd.\"" in page
    assert_clean(page)


@pytest.mark.parametrize("status", [FULFILLED, REJECTED, CANCELLED])
def test_finished_orders_have_no_actions(client, login, owner, make, status):
    order = make(status)
    login(owner)
    page = get(client, f"/app/orders/{order.number}")
    assert to_statuses(page) == []
    assert "<textarea" not in page
    assert "nothing else to do" in page
    assert ("/invoice" in page) == (status == FULFILLED)
    assert_clean(page)


def test_rejected_order_shows_who_rejected_it_and_why(client, login, owner, make):
    order = make(REJECTED, reason="Out of stock until\nthe 15th.")
    login(owner)
    page = get(client, f"/app/orders/{order.number}")
    assert f"<strong>Rejected</strong> by {owner.full_name} on 1 Nov 2026, 12:30 AM" in page
    assert '<p class="order-note">Out of stock until\nthe 15th.</p>' in page


def test_cancelled_order_shows_who_cancelled_it_and_the_note(client, login, owner, admin, make):
    order = make(APPROVED)
    login(admin)
    post_status(client, order, {"to_status": CANCELLED, "message": "Supplier shut down."})
    page = get(client, f"/app/orders/{order.number}")
    assert f"<strong>Cancelled</strong> by {admin.full_name} on" in page
    assert '<p class="order-note">Supplier shut down.</p>' in page
    assert "Cancelled by " + admin.full_name in page  # timeline


def test_missing_details_read_naturally(client, login, owner, company, carton):
    person = factories.make_client(company, email="solo@example.com", first_name="Ravi", last_name=None)
    person.buyer_company = None
    person.active = False
    db.session.commit()
    order = make_order(company, person, [(carton, 1)])
    login(owner)
    page = get(client, f"/app/orders/{order.number}")
    assert "by Ravi" in page
    assert '<dd class="kv__value">Not given</dd>' in page  # buyer company
    assert '<dd class="kv__value">Not given</dd></div>' in page.split("Phone")[1]
    assert "The buyer didn't add any notes to this order." in page
    assert "badge--warning" in page and "Suspended" in page
    assert "1 product<" in page
    assert_clean(page)


def test_detail_shows_order_lines_as_ordered_even_after_the_product_changes(client, login, owner, make, carton):
    order = make(PENDING)
    carton.name, carton.price_paise, carton.gst_rate_bp = "Carton (new)", 1, 0
    db.session.commit()
    login(owner)
    page = get(client, f"/app/orders/{order.number}")
    assert "Carton, 5-ply" in page and "Carton (new)" not in page
    assert "₹42.50" in page and "₹5,015.00" in page


def test_detail_runs_a_fixed_number_of_queries(client, login, owner, company, buyer):
    products = [factories.make_product(company) for _ in range(6)]
    order = make_order(company, buyer, [(p, 2) for p in products], FULFILLED, actor=owner)
    login(owner)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        get(client, f"/app/orders/{order.number}")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    # user, company, order + client + deciders, items, events + actors, sidebar pending count
    assert len(statements) <= 7


@pytest.mark.parametrize("path", ["ORD-999999", "ord-000001", "%00", "x" * 40, "ORD-000001%20"])
def test_unknown_orders_are_404(client, login, owner, make, path):
    make(PENDING)
    login(owner)
    assert client.get(f"/app/orders/{path}").status_code == 404
    assert client.get(f"/app/orders/{path}/invoice").status_code == 404
    assert client.post(f"/app/orders/{path}/status", data={"to_status": APPROVED}).status_code == 404


def test_other_companies_orders_are_404(client, login, owner, make, other_company, mail):
    make(PENDING)  # our ORD-000001
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 1)])
    rival_second = make_order(rival["company"], rival["client"], [(rival["product"], 1)])  # ORD-000002
    login(owner)
    for path in (f"/app/orders/{rival_second.number}", f"/app/orders/{rival_second.number}/invoice"):
        assert client.get(path).status_code == 404
    response = post_status(client, rival_second, {"to_status": APPROVED})
    assert response.status_code == 404
    db.session.refresh(rival_second)
    assert rival_second.status == PENDING
    assert mail == []


def test_only_staff_can_open_or_change_orders(client, login, buyer, make):
    order = make(PENDING)
    response = client.get(f"/app/orders/{order.number}")
    assert response.headers["Location"].startswith("/login")
    response = post_status(client, order, {"to_status": CANCELLED})
    assert response.headers["Location"].startswith("/login")

    login(buyer)
    assert client.get(f"/app/orders/{order.number}").headers["Location"] == "/portal/"
    assert client.get(f"/app/orders/{order.number}/invoice").headers["Location"] == "/portal/"
    response = post_status(client, order, {"to_status": CANCELLED})
    assert response.headers["Location"] == "/portal/"
    db.session.refresh(order)
    assert order.status == PENDING


def test_status_changes_need_post(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    assert client.get(f"/app/orders/{order.number}/status?to_status=approved").status_code == 405
    db.session.refresh(order)
    assert order.status == PENDING


# Status changes --------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_approve(client, login, request, buyer, make, mail, who):
    user = request.getfixturevalue(who)
    order = make(PENDING)
    login(user)
    response = post_status(client, order, {"to_status": APPROVED})
    assert response.headers["Location"] == f"/app/orders/{order.number}"
    page = follow(client, response)
    assert flashes(page) == [f"Approved {order.number}. Mark it as fulfilled once the goods are delivered."]
    db.session.refresh(order)
    assert order.status == APPROVED
    assert order.decided_by_id == user.id and order.decided_at is not None
    assert [e.to_status for e in order.events] == [PENDING, APPROVED]
    assert order.events[-1].actor_id == user.id
    assert [m.to for m in mail] == [[buyer.email]]
    assert to_statuses(page) == [CANCELLED, FULFILLED]


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_reject_with_a_reason(client, login, request, buyer, make, mail, who):
    order = make(PENDING)
    login(request.getfixturevalue(who))
    response = post_status(client, order, {"to_status": REJECTED, "message": "  We're out of 5-ply.\r\nTry 3-ply.  "})
    page = follow(client, response)
    assert flashes(page) == [f"Rejected {order.number}. Your reason is saved on the order for the client to see."]
    db.session.refresh(order)
    assert order.status == REJECTED
    assert order.rejection_reason == "We're out of 5-ply.\nTry 3-ply."
    assert order.events[-1].message == "We're out of 5-ply.\nTry 3-ply."
    assert len(mail) == 1 and "out of 5-ply" in mail[0].text


@pytest.mark.parametrize("reason", ["", "   ", "\r\n\t", None])
def test_reject_without_a_reason_shows_the_form_again(client, login, owner, make, mail, reason):
    order = make(PENDING)
    login(owner)
    data = {"to_status": REJECTED}
    if reason is not None:
        data["message"] = reason
    response = post_status(client, order, data)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert (
        '<p class="field__error" id="message-error">Add a reason so the client knows why the order was rejected.</p>'
        in page
    )
    assert 'aria-invalid="true"' in page
    assert to_statuses(page) == [APPROVED, REJECTED]
    db.session.refresh(order)
    assert order.status == PENDING and event_count(order) == 1
    assert mail == []
    assert_clean(page)


def test_too_long_reason_keeps_what_was_typed(client, login, owner, make, mail):
    order = make(PENDING)
    login(owner)
    reason = "Sorry, " + "x" * 1000
    response = post_status(client, order, {"to_status": REJECTED, "message": reason})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Keep this to 1,000 characters or fewer." in page
    assert reason in page
    db.session.refresh(order)
    assert order.status == PENDING and mail == []


def test_a_reason_of_exactly_1000_characters_with_windows_newlines_is_accepted(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    # 500 lines of "x" joined by CRLF: 1,499 characters as sent, 999 once newlines are normalised
    reason = "\r\n".join(["x"] * 500) + "y"
    assert len(reason) > 1000
    response = post_status(client, order, {"to_status": REJECTED, "message": reason})
    assert response.status_code == 302
    db.session.refresh(order)
    assert order.status == REJECTED and len(order.rejection_reason) == 1000


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_mark_fulfilled(client, login, request, make, mail, who):
    user = request.getfixturevalue(who)
    order = make(APPROVED)
    login(user)
    page = follow(client, post_status(client, order, {"to_status": FULFILLED}))
    assert flashes(page) == [f"Marked {order.number} as fulfilled."]
    db.session.refresh(order)
    assert order.status == FULFILLED
    assert order.fulfilled_by_id == user.id and order.fulfilled_at is not None
    assert len(mail) == 1
    assert to_statuses(page) == []


@pytest.mark.parametrize("note, saved", [("", None), ("  Stock ran out.  ", "Stock ran out.")])
def test_cancel_an_approved_order(client, login, admin, make, mail, note, saved):
    order = make(APPROVED)
    login(admin)
    page = follow(client, post_status(client, order, {"to_status": CANCELLED, "message": note}))
    assert flashes(page) == [f"Cancelled {order.number}."]
    db.session.refresh(order)
    assert order.status == CANCELLED and order.cancelled_at is not None
    assert order.events[-1].message == saved
    assert len(mail) == 1


def test_too_long_cancel_note_keeps_what_was_typed(client, login, owner, make):
    order = make(APPROVED)
    login(owner)
    note = "n" * 1001
    response = post_status(client, order, {"to_status": CANCELLED, "message": note})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert note in page and "Keep this to 1,000 characters or fewer." in page
    assert to_statuses(page) == [CANCELLED, FULFILLED]
    db.session.refresh(order)
    assert order.status == APPROVED


def test_a_note_on_approval_is_kept_and_a_too_long_one_is_refused(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    page = follow(client, post_status(client, order, {"to_status": APPROVED, "message": "x" * 1001}))
    assert flashes(page) == ["Keep this to 1,000 characters or fewer."]
    db.session.refresh(order)
    assert order.status == PENDING

    follow(client, post_status(client, order, {"to_status": APPROVED, "message": "Dispatching Monday."}))
    db.session.refresh(order)
    assert order.status == APPROVED and order.events[-1].message == "Dispatching Monday."


INVALID = [
    (PENDING, PENDING, "is already pending review, so nothing changed."),
    (PENDING, FULFILLED, "hasn't been approved yet. Approve it before marking it as fulfilled."),
    (PENDING, CANCELLED, "is still pending review. To turn it down, reject it with a reason for the client."),
    (APPROVED, APPROVED, "is already approved, so nothing changed."),
    (APPROVED, REJECTED, "is approved, so it can't be rejected."),
    (APPROVED, PENDING, "is approved, so it can't be moved back to pending review."),
    (FULFILLED, FULFILLED, "is already fulfilled, so nothing changed."),
    (FULFILLED, CANCELLED, "is fulfilled, so it can't be cancelled."),
    (FULFILLED, APPROVED, "is fulfilled, so it can't be approved."),
    (FULFILLED, REJECTED, "is fulfilled, so it can't be rejected."),
    (FULFILLED, PENDING, "is fulfilled, so it can't be moved back to pending review."),
    (REJECTED, APPROVED, "is rejected, so it can't be approved."),
    (REJECTED, REJECTED, "is already rejected, so nothing changed."),
    (REJECTED, FULFILLED, "is rejected, so it can't be marked as fulfilled."),
    (REJECTED, CANCELLED, "is rejected, so it can't be cancelled."),
    (REJECTED, PENDING, "is rejected, so it can't be moved back to pending review."),
    (CANCELLED, APPROVED, "is cancelled, so it can't be approved."),
    (CANCELLED, REJECTED, "is cancelled, so it can't be rejected."),
    (CANCELLED, FULFILLED, "is cancelled, so it can't be marked as fulfilled."),
    (CANCELLED, CANCELLED, "is already cancelled, so nothing changed."),
    (CANCELLED, PENDING, "is cancelled, so it can't be moved back to pending review."),
]


def test_invalid_list_covers_every_disallowed_change():
    allowed = {(PENDING, APPROVED), (PENDING, REJECTED), (APPROVED, FULFILLED), (APPROVED, CANCELLED)}
    every = {(a, b) for a in ORDER_STATUSES for b in ORDER_STATUSES}
    assert {(a, b) for a, b, _ in INVALID} == every - allowed


@pytest.mark.parametrize("who", ["owner", "admin"])
@pytest.mark.parametrize("current, target, message", INVALID)
def test_invalid_changes_are_refused_without_changing_anything(
    client, login, request, make, mail, who, current, target, message
):
    order = make(current)
    before = event_count(order)
    login(request.getfixturevalue(who))
    response = post_status(client, order, {"to_status": target, "message": "A note"})
    assert response.headers["Location"] == f"/app/orders/{order.number}"
    page = follow(client, response)
    assert flashes(page) == [f"{order.number} {message}"]
    assert "badge--success" not in page and "alert--success" not in page
    db.session.refresh(order)
    assert order.status == current
    assert event_count(order) == before
    assert mail == []


@pytest.mark.parametrize("value", ["shipped", "", "  ", "approved; drop table orders", "ＡＰＰＲＯＶＥＤ", None])
def test_unknown_target_status_is_a_friendly_error(client, login, owner, make, mail, value):
    order = make(PENDING)
    login(owner)
    data = {} if value is None else {"to_status": value}
    page = follow(client, post_status(client, order, data))
    assert flashes(page) == ["We didn't recognise that change. Use the buttons on this page to update the order."]
    db.session.refresh(order)
    assert order.status == PENDING and mail == []


def test_target_status_is_read_case_insensitively(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    follow(client, post_status(client, order, {"to_status": " Approved "}))
    db.session.refresh(order)
    assert order.status == APPROVED


def test_double_click_approves_once(client, login, owner, make, mail):
    order = make(PENDING)
    login(owner)
    follow(client, post_status(client, order, {"to_status": APPROVED}))
    page = follow(client, post_status(client, order, {"to_status": APPROVED}))
    assert flashes(page) == [f"{order.number} is already approved, so nothing changed."]
    assert "alert--info" in page
    db.session.refresh(order)
    assert [e.to_status for e in order.events] == [PENDING, APPROVED]
    assert len(mail) == 1


def test_odd_form_data_never_fails(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    response = client.post(
        f"/app/orders/{order.number}/status",
        data={"to_status[]": APPROVED, "message[x]": "y", "csrf_token": "", "unexpected": "1"},
    )
    assert flashes(follow(client, response)) == [
        "We didn't recognise that change. Use the buttons on this page to update the order."
    ]
    # Repeated fields: the first value counts
    response = client.post(
        f"/app/orders/{order.number}/status",
        data=MultiDict([("to_status", REJECTED), ("to_status", APPROVED), ("message", "No")]),
    )
    page = follow(client, response)
    db.session.refresh(order)
    assert order.status == REJECTED and order.rejection_reason == "No"
    assert flashes(page) == [f"Rejected {order.number}. Your reason is saved on the order for the client to see."]


def test_a_problem_reported_by_the_order_service_is_shown(client, login, owner, make, monkeypatch, mail):
    order = make(PENDING)

    def refuse(*args, **kwargs):
        raise OrderError("This order changed while you were looking at it.")

    monkeypatch.setattr(orders_view, "transition_order", refuse)
    login(owner)
    page = follow(client, post_status(client, order, {"to_status": APPROVED}))
    assert flashes(page) == ["This order changed while you were looking at it."]
    db.session.refresh(order)
    assert order.status == PENDING


def test_status_changes_require_a_csrf_token(app, client, login, owner, make):
    order = make(PENDING)
    login(owner)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = post_status(client, order, {"to_status": APPROVED})
        assert response.status_code == 400
        db.session.refresh(order)
        assert order.status == PENDING

        page = get(client, f"/app/orders/{order.number}")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        # The reject form carries its own token too
        assert page.count('name="csrf_token"') >= 3  # sign out, approve, reject
        response = post_status(client, order, {"to_status": APPROVED, "csrf_token": token})
        assert response.status_code == 302
        db.session.refresh(order)
        assert order.status == APPROVED
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_clean_note():
    assert orders_view.clean_note("  a\r\nb\rc\n ") == "a\nb\nc"
    assert orders_view.clean_note(None) is None


# Invoice -------------------------------------------------------------------------------


def test_proforma_invoice(client, login, admin, company, make, carton):
    company.legal_name = "Kaveri Supplies Private Limited"
    company.gstin = "29ABCDE1234F1Z5"
    company.address = "14, 2nd Cross, Peenya\nBengaluru 560058"
    company.phone = "+91 80 4000 1234"
    company.support_email = "orders@kaveri.example"
    db.session.commit()
    order = make(APPROVED, notes="PO 4512")
    carton.name = "Renamed later"
    db.session.commit()

    login(admin)
    page = get(client, f"/app/orders/{order.number}/invoice")
    assert f"<title>Proforma invoice {order.number}" in page
    assert "Kaveri Supplies Private Limited" in page
    assert "29ABCDE1234F1Z5" in page
    assert "14, 2nd Cross, Peenya\nBengaluru 560058" in page
    assert "+91 80 4000 1234 · orders@kaveri.example" in page
    assert "Buyer Pvt Ltd" in page and "buyer@sunrise.example" in page
    assert "31 Oct 2026" in page
    assert "Carton, 5-ply" in page and "Renamed later" not in page
    assert "₹4,250.00" in page and "₹765.00" in page and "₹5,015.00" in page
    assert "PO 4512" in page
    assert "It is not a tax invoice." in page
    assert f'href="/app/orders/{order.number}"' in page and "Back to order" in page
    assert "data-print" in page
    assert_clean(page)


def test_invoice_without_optional_company_details(client, login, owner, make):
    order = make(PENDING)
    login(owner)
    page = get(client, f"/app/orders/{order.number}/invoice")
    assert "Kaveri Supplies" in page
    assert "GSTIN" not in page
    assert_clean(page)
