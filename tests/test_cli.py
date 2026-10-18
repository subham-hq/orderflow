from orderflow.extensions import db
from orderflow.models import Bulletin, Company, Order, Product, User


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
