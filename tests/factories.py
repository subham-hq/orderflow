"""Helpers for creating test data."""

from itertools import count

from orderflow.extensions import db
from orderflow.models import ADMIN, CLIENT, OWNER, Company, Product, User
from orderflow.timeutil import utcnow

PASSWORD = "a long test password"
_seq = count(1)


def make_company(name=None, **fields):
    company = Company(name=name or f"Supplier {next(_seq)}", **fields)
    db.session.add(company)
    db.session.commit()
    return company


def make_user(company, role=OWNER, email=None, password=PASSWORD, verified=True, **fields):
    n = next(_seq)
    fields.setdefault("first_name", f"User{n}")
    fields.setdefault("last_name", "Test")
    user = User(
        company=company,
        email=email or f"user{n}@example.com",
        role=role,
        email_verified_at=utcnow() if verified else None,
        **fields,
    )
    if password:
        user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def make_owner(company=None, **fields):
    return make_user(company or make_company(), role=OWNER, **fields)


def make_admin(company, **fields):
    return make_user(company, role=ADMIN, **fields)


def make_client(company, **fields):
    fields.setdefault("buyer_company", "Buyer Pvt Ltd")
    return make_user(company, role=CLIENT, **fields)


def make_product(company, sku=None, price_paise=10_000, gst_rate_bp=1800, **fields):
    n = next(_seq)
    fields.setdefault("name", f"Product {n}")
    product = Product(
        company=company,
        sku=sku or f"SKU-{n}",
        price_paise=price_paise,
        gst_rate_bp=gst_rate_bp,
        **fields,
    )
    db.session.add(product)
    db.session.commit()
    return product
