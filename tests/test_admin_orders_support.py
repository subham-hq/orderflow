"""Helpers shared by the dashboard, analytics and order management tests.

This module holds no tests. Orders are built directly (not through
place_order) so tests can pin placed_at and the status history exactly.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED, Order, OrderEvent, OrderItem
from orderflow.money import gst_amount
from orderflow.timeutil import utcnow

IST = ZoneInfo("Asia/Kolkata")


def ist(*parts):
    """An India-time wall clock reading, as the UTC instant the database stores."""
    return datetime(*parts, tzinfo=IST).astimezone(UTC)


def make_order(company, client, items, status=PENDING, placed_at=None, actor=None, notes=None, reason=None):
    """Create an order with snapshot lines and a status history that matches `status`.

    items: list of (product, quantity). actor: the staff member who decided it.
    """
    placed_at = placed_at or utcnow()
    seq = company.next_order_seq or 1
    company.next_order_seq = seq + 1
    order = Order(
        company_id=company.id,
        client_id=client.id,
        number=f"{company.order_prefix or 'ORD'}-{seq:06d}",
        status=status,
        notes=notes,
        placed_at=placed_at,
    )
    for product, quantity in items:
        subtotal = product.price_paise * quantity
        tax = gst_amount(subtotal, product.gst_rate_bp)
        order.items.append(
            OrderItem(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                category=product.category,
                unit=product.unit or "unit",
                unit_price_paise=product.price_paise,
                gst_rate_bp=product.gst_rate_bp,
                quantity=quantity,
                line_subtotal_paise=subtotal,
                line_tax_paise=tax,
                line_total_paise=subtotal + tax,
            )
        )
    order.subtotal_paise = sum(item.line_subtotal_paise for item in order.items)
    order.tax_paise = sum(item.line_tax_paise for item in order.items)
    order.total_paise = order.subtotal_paise + order.tax_paise
    order.events.append(OrderEvent(actor_id=client.id, to_status=PENDING, created_at=placed_at))

    later = placed_at + timedelta(hours=1)
    actor_id = actor.id if actor else None
    if status in (APPROVED, FULFILLED, REJECTED):
        decided = REJECTED if status == REJECTED else APPROVED
        order.decided_at, order.decided_by_id = later, actor_id
        if decided == REJECTED:
            order.rejection_reason = reason or "Out of stock"
        order.events.append(
            OrderEvent(
                actor_id=actor_id,
                from_status=PENDING,
                to_status=decided,
                message=order.rejection_reason,
                created_at=later,
            )
        )
    if status == FULFILLED:
        done = later + timedelta(hours=1)
        order.fulfilled_at, order.fulfilled_by_id = done, actor_id
        order.events.append(OrderEvent(actor_id=actor_id, from_status=APPROVED, to_status=FULFILLED, created_at=done))
    if status == CANCELLED:
        order.cancelled_at = later
        order.events.append(OrderEvent(actor_id=client.id, from_status=PENDING, to_status=CANCELLED, created_at=later))

    db.session.add(order)
    db.session.commit()
    return order
