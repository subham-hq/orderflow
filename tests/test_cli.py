from orderflow.extensions import db
from orderflow.models import Bulletin, Company, Order, Product, User
from tests import factories


def test_seed_demo_builds_a_consistent_company(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    assert "demo-password-123" in result.output

    company = db.session.scalar(db.select(Company))
    assert db.session.scalar(db.select(db.func.count(Product.id))) == 12
    assert db.session.scalar(db.select(db.func.count(User.id))) == 6
    assert db.session.scalar(db.select(db.func.count(Bulletin.id))) == 1

    orders = db.session.scalars(db.select(Order)).all()
    assert len(orders) == 24
    assert company.next_order_seq == 25
    for order in orders:
        assert order.subtotal_paise == sum(item.line_subtotal_paise for item in order.items)
        assert order.tax_paise == sum(item.line_tax_paise for item in order.items)
        assert order.total_paise == order.subtotal_paise + order.tax_paise
        assert order.events[0].to_status == "pending"
        assert order.events[-1].to_status == order.status

    again = runner.invoke(args=["seed-demo"])
    assert again.exit_code != 0  # refuses to duplicate the demo owner
    assert "These emails already have accounts: ananya@sunrise-retail.example," in again.output
    assert "Use a different --email to add another demo company." in again.output


def test_seed_demo_adds_a_second_company_with_another_email(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo"]).exit_code == 0

    result = runner.invoke(args=["seed-demo", "--email", " Rival@Rival.example "])
    assert result.exit_code == 0, result.output
    assert "ananya+rival-rival-example@sunrise-retail.example" in result.output

    assert db.session.scalar(db.select(db.func.count(Company.id))) == 2
    assert db.session.scalar(db.select(db.func.count(User.id))) == 12
    rival = db.session.scalar(db.select(User).where(User.email == "rival@rival.example"))
    emails = set(db.session.scalars(db.select(User.email).where(User.company_id == rival.company_id)))
    assert emails == {
        "rival@rival.example",
        "ops.rival@rival.example",
        "ananya+rival-rival-example@sunrise-retail.example",
        "vikram+rival-rival-example@mehta-hardware.example",
        "fatima+rival-rival-example@greenleaf-foods.example",
        "joseph+rival-rival-example@coastline-hotels.example",
    }
    numbers = db.session.scalars(db.select(Order.number).where(Order.company_id == rival.company_id)).all()
    assert len(numbers) == 24 and "ORD-000001" in numbers  # numbered per company


def test_seed_demo_names_the_email_in_the_way_without_a_traceback(app):
    factories.make_client(factories.make_company(), email="vikram@mehta-hardware.example", password=None)
    runner = app.test_cli_runner()

    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code == 1
    assert "vikram@mehta-hardware.example already has an account" in result.output
    assert isinstance(result.exception, SystemExit)  # a clean message, not an IntegrityError
    assert db.session.scalar(db.select(db.func.count(Company.id))) == 1  # nothing was added

    bad = runner.invoke(args=["seed-demo", "--email", "not-an-email"])
    assert bad.exit_code == 2 and "Enter an email address" in bad.output
