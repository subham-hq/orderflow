"""Database models.

Every record that belongs to a business (products, orders, bulletins, users)
carries company_id. Queries must always filter by the signed-in user's
company; use the helpers in orderflow.permissions rather than raw ids.

Money is stored as integer paise. GST rates are stored in basis points
(18% = 1800) so tax maths never touches floats.
"""

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .timeutil import utcnow

# Roles
OWNER = "owner"  # created the company account; cannot be suspended or removed
ADMIN = "admin"  # staff member of the supplier
CLIENT = "client"  # buyer who places orders
ROLES = (OWNER, ADMIN, CLIENT)
STAFF_ROLES = (OWNER, ADMIN)

# Order statuses
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
FULFILLED = "fulfilled"
CANCELLED = "cancelled"
ORDER_STATUSES = (PENDING, APPROVED, REJECTED, FULFILLED, CANCELLED)

# Orders that count as revenue
REVENUE_STATUSES = (APPROVED, FULFILLED)

# Largest id an INTEGER primary key can hold. Postgres rejects a bigger
# number in a query (instead of finding no row), so check ids from URLs first.
MAX_ID = 2_147_483_647

# Which status changes are allowed, and who may make them
STAFF_TRANSITIONS = {
    PENDING: (APPROVED, REJECTED),
    APPROVED: (FULFILLED, CANCELLED),
}
CLIENT_TRANSITIONS = {
    PENDING: (CANCELLED,),
}


def _status_check(column, values):
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class Company(db.Model):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    legal_name: Mapped[str | None] = mapped_column(String(160))
    gstin: Mapped[str | None] = mapped_column(String(15))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32))
    support_email: Mapped[str | None] = mapped_column(String(254))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    order_prefix: Mapped[str] = mapped_column(String(8), default="ORD")
    next_order_seq: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="company")

    def __repr__(self):
        return f"<Company {self.id} {self.name!r}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_status_check("role", ROLES), name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20), default=CLIENT)
    # For clients: the buyer's business name
    buyer_company: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change, reset and suspension to end every session
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    invite_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[Company] = relationship(back_populates="users")
    invited_by: Mapped["User | None"] = relationship(remote_side=[id])

    @validates("email")
    def _normalize_email(self, key, value):
        # Always stored trimmed and in lower case, so sign-in can look the
        # address up exactly (using the unique index) and still ignore case.
        return value.strip().lower() if isinstance(value, str) else value

    # Flask-Login
    def get_id(self):
        return f"{self.id}:{self.auth_version}"

    @property
    def is_active(self):
        return self.active

    # Passwords
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash or not password:
            return False
        return check_password_hash(self.password_hash, password)

    def end_all_sessions(self):
        """Invalidate every existing session and remember-me cookie."""
        self.auth_version = (self.auth_version or 1) + 1

    # Role helpers
    @property
    def is_owner(self):
        return self.role == OWNER

    @property
    def is_staff(self):
        return self.role in STAFF_ROLES

    @property
    def is_client(self):
        return self.role == CLIENT

    @property
    def is_verified(self):
        return self.email_verified_at is not None

    @property
    def has_accepted_invite(self):
        return self.password_hash is not None

    @property
    def full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    @property
    def initials(self):
        letters = [part[0] for part in (self.first_name, self.last_name) if part]
        return "".join(letters).upper() or "?"

    def __repr__(self):
        return f"<User {self.id} {self.email} {self.role}>"


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("company_id", "sku", name="uq_products_company_sku"),
        CheckConstraint("price_paise >= 0", name="price_non_negative"),
        CheckConstraint("gst_rate_bp >= 0 AND gst_rate_bp <= 10000", name="gst_rate_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(20), default="unit")
    price_paise: Mapped[int] = mapped_column(BigInteger)
    gst_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    company: Mapped[Company] = relationship()

    def __repr__(self):
        return f"<Product {self.id} {self.sku}>"


class Order(db.Model):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_orders_company_number"),
        UniqueConstraint("client_id", "submission_key", name="uq_orders_client_submission"),
        CheckConstraint(_status_check("status", ORDER_STATUSES), name="status"),
        Index("ix_orders_company_status", "company_id", "status"),
        Index("ix_orders_company_placed_at", "company_id", "placed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default=PENDING)
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    # One-time key from the checkout form, so a double submit can't create two orders
    submission_key: Mapped[str | None] = mapped_column(String(64))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship()
    client: Mapped[User] = relationship(foreign_keys=[client_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])
    fulfilled_by: Mapped[User | None] = relationship(foreign_keys=[fulfilled_by_id])
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.id"
    )

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items)

    def __repr__(self):
        return f"<Order {self.number} {self.status}>"


class OrderItem(db.Model):
    """A line on an order. Product details are copied at the time of ordering,
    so later edits to the catalog never change past orders or invoices."""

    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(20), default="unit")
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)
    gst_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[int] = mapped_column(Integer)
    line_subtotal_paise: Mapped[int] = mapped_column(BigInteger)
    line_tax_paise: Mapped[int] = mapped_column(BigInteger)
    line_total_paise: Mapped[int] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()


class OrderEvent(db.Model):
    """Timeline entry: who changed an order, when, and why."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship()


class Bulletin(db.Model):
    """An announcement shown on client dashboards."""

    __tablename__ = "bulletins"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    company: Mapped[Company] = relationship()
    author: Mapped[User | None] = relationship()

    def is_live(self, now=None):
        from .timeutil import as_utc

        now = now or utcnow()
        return self.is_published and (self.expires_at is None or as_utc(self.expires_at) > now)


class Lead(db.Model):
    """An enquiry sent from the public contact form."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    company: Mapped[str | None] = mapped_column(String(160))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
