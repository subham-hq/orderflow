"""Command-line tools: `flask seed-demo` and `flask leads`."""

import random
from datetime import timedelta

import click

from .extensions import db
from .models import ADMIN, CLIENT, OWNER, Bulletin, Company, Lead, Order, OrderEvent, Product, User
from .services.orders import price_cart
from .timeutil import format_datetime, utcnow

DEMO_PASSWORD = "demo-password-123"

DEMO_PRODUCTS = [
    # sku, name, category, unit, price (paise), GST (basis points)
    ("CTN-001", "Corrugated carton, 5-ply, 18 x 12 x 12 in", "Packaging", "box", 4_250, 1800),
    ("CTN-002", "Corrugated carton, 3-ply, 12 x 10 x 8 in", "Packaging", "box", 2_175, 1800),
    ("TAP-010", "BOPP tape, 48 mm x 65 m, clear", "Packaging", "roll", 6_800, 1800),
    ("STR-200", "Stretch film, 18 in x 23 micron", "Packaging", "roll", 41_500, 1800),
    ("BLT-M10", "Hex bolt M10 x 50, zinc plated", "Fasteners", "pack of 100", 52_000, 1800),
    ("NUT-M10", "Hex nut M10, zinc plated", "Fasteners", "pack of 100", 18_500, 1800),
    ("WSH-M10", "Flat washer M10", "Fasteners", "pack of 100", 9_900, 1800),
    ("GLV-NIT", "Nitrile gloves, powder free, large", "Safety", "box of 100", 39_900, 1200),
    ("HLM-IND", "Industrial safety helmet, ISI marked", "Safety", "piece", 24_900, 1800),
    ("CLN-5L", "Floor cleaner concentrate", "Housekeeping", "5 L can", 64_000, 1800),
    ("RCE-25K", "Sona Masoori rice, 25 kg", "Staples", "bag", 1_45_000, 500),
    ("OIL-15L", "Refined sunflower oil", "Staples", "15 L tin", 2_18_000, 500),
]

DEMO_CLIENTS = [
    ("Ananya", "Rao", "ananya@sunrise-retail.example", "Sunrise Retail LLP"),
    ("Vikram", "Mehta", "vikram@mehta-hardware.example", "Mehta Hardware Stores"),
    ("Fatima", "Sheikh", "fatima@greenleaf-foods.example", "Greenleaf Foods"),
    ("Joseph", "Mathew", "joseph@coastline-hotels.example", "Coastline Hotels"),
]


def init_cli(app):
    @app.cli.command("leads")
    @click.option("--limit", default=20, show_default=True, type=click.IntRange(1, 1000), help="How many to list.")
    def list_leads(limit):
        """List the latest enquiries from the contact form, newest first."""
        leads = db.session.scalars(db.select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(limit)).all()
        if not leads:
            click.echo("No enquiries yet.")
            return
        for lead in leads:
            when = format_datetime(lead.created_at, app.config["DEFAULT_TIMEZONE"])
            company = f", {lead.company}" if lead.company else ""
            click.echo(f"#{lead.id}  {when}  {lead.name} <{lead.email}>{company}")
            for line in (lead.message or "").splitlines():
                click.echo(f"    {line}")
            click.echo()

    @app.cli.command("seed-demo")
    @click.option("--email", default="owner@orderflow.example", show_default=True, help="Owner sign-in email.")
    @click.option("--force", is_flag=True, help="Add the demo company even if that email exists.")
    def seed_demo(email, force):
        """Create a demo company with products, clients and orders."""
        if db.session.scalar(db.select(User).where(User.email == email)) and not force:
            raise click.ClickException(f"{email} already exists. Use --force with a different --email.")

        rng = random.Random(42)
        now = utcnow()
        company = Company(
            name="Kaveri Supplies",
            legal_name="Kaveri Supplies Private Limited",
            gstin="29ABCDE1234F1Z5",
            address="14, 2nd Cross, Peenya Industrial Area\nBengaluru, Karnataka 560058",
            phone="+91 80 4000 1234",
            support_email="orders@kaveri.example",
        )
        owner = User(
            company=company, email=email, first_name="Priya", last_name="Nair", role=OWNER, email_verified_at=now
        )
        owner.set_password(DEMO_PASSWORD)
        staff = User(
            company=company,
            email=f"ops.{email}",
            first_name="Arjun",
            last_name="Das",
            role=ADMIN,
            email_verified_at=now,
        )
        staff.set_password(DEMO_PASSWORD)
        db.session.add_all([company, owner, staff])

        products = []
        for sku, name, category, unit, price, gst in DEMO_PRODUCTS:
            product = Product(
                company=company, sku=sku, name=name, category=category, unit=unit, price_paise=price, gst_rate_bp=gst
            )
            products.append(product)
        db.session.add_all(products)

        clients = []
        for first, last, client_email, buyer in DEMO_CLIENTS:
            client = User(
                company=company,
                email=client_email,
                first_name=first,
                last_name=last,
                buyer_company=buyer,
                role=CLIENT,
                invited_by=owner,
                email_verified_at=now,
            )
            client.set_password(DEMO_PASSWORD)
            clients.append(client)
        db.session.add_all(clients)
        db.session.flush()

        statuses = ["fulfilled"] * 14 + ["approved"] * 4 + ["pending"] * 4 + ["rejected", "cancelled"]
        for index, status in enumerate(statuses):
            client = rng.choice(clients)
            cart = {p.id: rng.randint(2, 40) for p in rng.sample(products, rng.randint(1, 4))}
            priced = price_cart(company.id, cart)
            placed = now - timedelta(days=rng.randint(0, 59), hours=rng.randint(0, 23))
            order = Order(
                company=company,
                client_id=client.id,
                number=f"ORD-{index + 1:06d}",
                status=status,
                subtotal_paise=priced.subtotal_paise,
                tax_paise=priced.tax_paise,
                total_paise=priced.total_paise,
                placed_at=placed,
            )
            from .models import OrderItem

            for line in priced.lines:
                order.items.append(
                    OrderItem(
                        product_id=line.product.id,
                        sku=line.product.sku,
                        name=line.product.name,
                        category=line.product.category,
                        unit=line.product.unit,
                        unit_price_paise=line.product.price_paise,
                        gst_rate_bp=line.product.gst_rate_bp,
                        quantity=line.quantity,
                        line_subtotal_paise=line.subtotal_paise,
                        line_tax_paise=line.tax_paise,
                        line_total_paise=line.total_paise,
                    )
                )
            order.events.append(OrderEvent(actor_id=client.id, to_status="pending", created_at=placed))
            if status in ("approved", "fulfilled", "rejected"):
                decided = placed + timedelta(hours=rng.randint(1, 20))
                order.decided_at, order.decided_by_id = decided, owner.id
                to = "rejected" if status == "rejected" else "approved"
                message = "Out of stock on the 5-ply cartons this week." if to == "rejected" else None
                order.rejection_reason = message
                order.events.append(
                    OrderEvent(
                        actor_id=owner.id, from_status="pending", to_status=to, message=message, created_at=decided
                    )
                )
            if status == "fulfilled":
                done = order.decided_at + timedelta(days=rng.randint(1, 3))
                order.fulfilled_at, order.fulfilled_by_id = done, staff.id
                order.events.append(
                    OrderEvent(actor_id=staff.id, from_status="approved", to_status="fulfilled", created_at=done)
                )
            if status == "cancelled":
                order.cancelled_at = placed + timedelta(hours=2)
                order.events.append(
                    OrderEvent(
                        actor_id=client.id, from_status="pending", to_status="cancelled", created_at=order.cancelled_at
                    )
                )
            db.session.add(order)
        company.next_order_seq = len(statuses) + 1

        db.session.add(
            Bulletin(
                company=company,
                author=owner,
                title="Diwali dispatch schedule",
                body="Orders approved by 25 October ship before the holiday.\nOur warehouse is closed from 31 October to 3 November.",
                expires_at=now + timedelta(days=21),
            )
        )
        db.session.commit()
        click.echo(f"Demo company created. Sign in as {email} or any client with password: {DEMO_PASSWORD}")
